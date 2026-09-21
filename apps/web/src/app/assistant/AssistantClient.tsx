"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, APIError, type ApiSession } from "@/lib/api";
import { Spinner } from "@/components/States";
import { formatSlotPath, formatCategory, clsx } from "@/lib/format";
import type { SearchResponseBody } from "@/lib/types";

interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  text: string;
  response?: SearchResponseBody;
  pending?: boolean;
  error?: string;
}

const STATE_TONE: Record<SearchResponseBody["state"], string> = {
  answer: "bg-emerald-50 text-emerald-700",
  needs_clarification: "bg-amber-50 text-amber-700",
  not_found: "bg-ink-100 text-ink-600",
  exists_but_not_placed: "bg-amber-50 text-amber-700",
  error: "bg-red-50 text-red-700",
};

const STATE_LABEL: Record<SearchResponseBody["state"], string> = {
  answer: "已找到",
  needs_clarification: "需要确认",
  not_found: "未找到",
  exists_but_not_placed: "未放置",
  error: "出错",
};

const INTENT_LABEL: Record<string, string> = {
  find_item: "查找物品",
  find_items: "批量查找",
  find_location: "查找位置",
  check_existence: "确认存在",
  list_category: "分类列表",
  suggest_placement: "收纳建议",
  unknown: "未理解",
};

export function AssistantClient({
  suggestions,
  session,
}: {
  suggestions: string[];
  session: ApiSession;
}) {
  const [turns, setTurns] = useState<ChatTurn[]>([
    {
      id: "welcome",
      role: "assistant",
      text: "你好！我是你的家庭收纳助手，能记住我们聊过的内容，你可以接着追问。\n试试问我：\n• 「我的数据线在哪里？」\n• 「客厅有哪些东西？」\n• 「我家还有没有电池？」",
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  // The backend remembers a conversation for us: it replays the recent turns
  // into the prompt, so 「那它放哪好？」 knows what 「它」 is. A ref rather than
  // state because `send` reads it synchronously and never renders it.
  const conversationId = useRef<string | undefined>(undefined);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    const userTurn: ChatTurn = {
      id: `u-${Date.now()}`,
      role: "user",
      text: trimmed,
    };
    const pendingTurn: ChatTurn = {
      id: `a-${Date.now()}`,
      role: "assistant",
      text: "",
      pending: true,
    };
    setTurns((prev) => [...prev, userTurn, pendingTurn]);
    setInput("");
    setBusy(true);
    try {
      const r = await api.search(
        { query: trimmed, conversation_id: conversationId.current },
        session,
      );
      conversationId.current = r.conversation_id;
      setTurns((prev) =>
        prev.map((t) =>
          t.id === pendingTurn.id
            ? {
                ...t,
                pending: false,
                text: r.answer_text,
                response: r,
              }
            : t,
        ),
      );
    } catch (err) {
      setTurns((prev) =>
        prev.map((t) =>
          t.id === pendingTurn.id
            ? {
                ...t,
                pending: false,
                text: "抱歉，AI 暂时无法回答，请稍后再试。",
                error: extractError(err, "请求失败"),
              }
            : t,
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card flex h-[70vh] flex-col overflow-hidden">
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {turns.map((t) =>
          t.role === "user" ? (
            <div key={t.id} className="flex justify-end">
              <div className="max-w-[80%] rounded-2xl rounded-tr-md bg-brand-500 px-3.5 py-2 text-sm text-white">
                {t.text}
              </div>
            </div>
          ) : (
            <AssistantBubble key={t.id} turn={t} />
          ),
        )}
        <div ref={endRef} />
      </div>

      {suggestions.length > 0 && turns.length <= 1 ? (
        <div className="border-t border-ink-100 px-4 py-3">
          <p className="mb-2 text-xs text-ink-500">试试这些问题：</p>
          <div className="flex flex-wrap gap-2">
            {suggestions.map((s) => (
              <button
                key={s}
                className="badge cursor-pointer hover:bg-brand-50 hover:text-brand-700"
                onClick={() => send(s)}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <form
        className="flex items-center gap-2 border-t border-ink-100 bg-white p-3"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          className="input flex-1"
          placeholder="问问 AI 助手…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={busy}
        />
        <button className="btn-primary" type="submit" disabled={busy || !input.trim()}>
          {busy ? <Spinner className="h-4 w-4" /> : null}
          发送
        </button>
      </form>
    </div>
  );
}

function AssistantBubble({ turn }: { turn: ChatTurn }) {
  const r = turn.response;
  return (
    <div className="flex justify-start">
      <div className="max-w-[90%] space-y-2 sm:max-w-[80%]">
        <div className="flex items-center gap-2">
          <span className="grid h-7 w-7 place-items-center rounded-full bg-amber-100 text-amber-600">
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 2 13.5 8.5 20 10l-6.5 1.5L12 18l-1.5-6.5L4 10l6.5-1.5L12 2Z" />
            </svg>
          </span>
          <p className="text-xs font-medium text-ink-500">AI 助手</p>
        </div>
        <div className="rounded-2xl rounded-tl-md bg-ink-100 px-3.5 py-2.5 text-sm text-ink-900">
          {turn.pending ? (
            <div className="flex items-center gap-2 text-ink-500">
              <Spinner className="h-4 w-4" /> 正在思考…
            </div>
          ) : (
            <p className="whitespace-pre-wrap">{turn.text}</p>
          )}
        </div>
        {turn.error ? <p className="text-xs text-red-600">{turn.error}</p> : null}
        {r && !turn.pending ? (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              <span
                className={clsx("rounded-full px-2 py-0.5 font-medium", STATE_TONE[r.state])}
              >
                {STATE_LABEL[r.state]}
              </span>
              <span className="badge">{INTENT_LABEL[r.intent] ?? r.intent}</span>
              {r.trace_id ? (
                <span className="badge font-mono text-[10px]">trace: {r.trace_id.slice(0, 8)}</span>
              ) : null}
            </div>
            {r.clarification_question ? (
              <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                💬 {r.clarification_question}
              </p>
            ) : null}
            {r.matches.length > 0 ? (
              <ul className="space-y-1.5">
                {r.matches.map((m) => (
                  <li
                    key={m.item_id}
                    className="rounded-lg border border-ink-100 bg-white px-3 py-2"
                  >
                    <div className="flex items-center justify-between">
                      <Link
                        href={`/items/${m.item_id}`}
                        className="text-sm font-medium text-ink-900 hover:text-brand-600 hover:underline"
                      >
                        {m.name || "未命名物品"}
                      </Link>
                      <span className="text-[11px] text-ink-500">
                        📍 {formatSlotPath(m.location)}
                      </span>
                    </div>
                    <p className="text-[11px] text-ink-400">
                      {[formatCategory(m.category), m.subcategory]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </li>
                ))}
              </ul>
            ) : null}
            {r.suggested_slot ? (
              <div className="rounded-lg border border-brand-200 bg-brand-50/60 px-3 py-2.5">
                <p className="text-[11px] font-medium text-brand-700">建议位置</p>
                <p className="mt-0.5 text-sm font-medium text-ink-900">
                  📍 {formatSlotPath(r.suggested_slot)}
                </p>
                {r.suggested_reason ? (
                  <p className="mt-1 text-[11px] text-ink-500">{r.suggested_reason}</p>
                ) : null}
                {r.suggested_item_name ? (
                  <Link
                    href={`/items/new?name=${encodeURIComponent(r.suggested_item_name)}`}
                    className="btn-secondary mt-2 !px-3 !py-1.5 text-xs"
                  >
                    添加「{r.suggested_item_name}」并确认位置
                  </Link>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function extractError(err: unknown, fallback: string): string {
  if (err instanceof APIError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}
