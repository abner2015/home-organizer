"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, APIError, type ApiSession } from "@/lib/api";
import { Spinner } from "@/components/States";
import type { CandidateView } from "@/lib/types";

// Preset reasons for a rejection (P0.4). The server stores the composed note on
// the recommendation's `candidates[0].audit_note` — it has no column of its own
// — and the *slot* is excluded from this item's future candidates regardless of
// what is written here, so the text is for the human reading the history.
const PRESET_REASONS = ["位置太远", "格子太小", "有更合适的地方", "其它"] as const;

export function RecommendationActions({
  recId,
  candidates,
  session,
}: {
  recId: string;
  candidates: CandidateView[];
  session: ApiSession;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [preset, setPreset] = useState<string>(PRESET_REASONS[0]);
  const [detail, setDetail] = useState("");

  async function accept(c: CandidateView) {
    setBusy(`accept:${c.slot_id}`);
    setError(null);
    try {
      await api.acceptRecommendation(recId, {}, session);
      router.push("/items");
      router.refresh();
    } catch (err) {
      setError(extract(err, "保存失败"));
    } finally {
      setBusy(null);
    }
  }

  function composedNote(): string {
    const extra = detail.trim();
    if (preset === "其它") return extra || "其它";
    return extra ? `${preset}（${extra}）` : preset;
  }

  async function reject() {
    setBusy("reject");
    setError(null);
    try {
      await api.rejectRecommendation(recId, { note: composedNote() }, session);
      router.refresh();
    } catch (err) {
      setError(extract(err, "操作失败"));
    } finally {
      setBusy(null);
    }
  }

  const recommended = candidates.find((c) => c.is_recommended) ?? candidates[0];
  return (
    <div className="mt-4 space-y-2">
      {error ? <p className="text-xs text-red-600">{error}</p> : null}
      {recommended ? (
        rejecting ? (
          <div className="space-y-2 rounded-lg bg-ink-50 p-3">
            <p className="text-xs font-medium text-ink-700">为什么不合适？</p>
            <div className="flex flex-wrap gap-2">
              {PRESET_REASONS.map((r) => (
                <button
                  key={r}
                  type="button"
                  className={preset === r ? "btn-primary" : "btn-secondary"}
                  onClick={() => setPreset(r)}
                  disabled={busy !== null}
                >
                  {r}
                </button>
              ))}
            </div>
            <input
              className="input"
              placeholder={preset === "其它" ? "请填写原因" : "补充说明（可选）"}
              value={detail}
              maxLength={200}
              onChange={(e) => setDetail(e.target.value)}
              disabled={busy !== null}
            />
            <div className="flex flex-wrap gap-2">
              <button
                className="btn-primary"
                onClick={reject}
                disabled={busy !== null || (preset === "其它" && !detail.trim())}
              >
                {busy === "reject" ? <Spinner className="h-4 w-4" /> : null}
                提交并排除该位置
              </button>
              <button
                className="btn-secondary"
                onClick={() => setRejecting(false)}
                disabled={busy !== null}
              >
                取消
              </button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <button
              className="btn-primary"
              onClick={() => accept(recommended)}
              disabled={busy !== null}
            >
              {busy === `accept:${recommended.slot_id}` ? <Spinner className="h-4 w-4" /> : null}
              确认放在这里
            </button>
            <button
              className="btn-secondary"
              onClick={() => setRejecting(true)}
              disabled={busy !== null}
            >
              不合适
            </button>
          </div>
        )
      ) : null}
    </div>
  );
}

function extract(err: unknown, fallback: string): string {
  if (err instanceof APIError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}
