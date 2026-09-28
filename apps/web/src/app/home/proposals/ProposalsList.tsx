"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import Link from "next/link";
import { api, APIError, type ApiSession } from "@/lib/api";
import { getSession } from "@/lib/session";
import { Spinner } from "@/components/States";
import type {
  ProposalSource,
  ProposalWarning,
  StructureProposalRow,
  StructureProposalStatus,
} from "@/lib/types";

const SOURCE_LABEL: Record<ProposalSource, string> = {
  photo: "照片",
  text: "描述",
  template: "模板",
};

const STATUS_LABEL: Record<StructureProposalStatus, string> = {
  pending: "待处理",
  accepted: "已接受",
  rejected: "已拒绝",
  superseded: "已替换",
};

const STATUS_TONE: Record<StructureProposalStatus, string> = {
  pending: "badge-brand",
  accepted: "bg-emerald-50 text-emerald-700 border-emerald-100",
  rejected: "bg-ink-100 text-ink-600 border-ink-200",
  superseded: "bg-ink-100 text-ink-500 border-ink-200",
};

const WARNING_LABEL: Record<string, string> = {
  truncated: "内容过多，已按上限裁剪",
  duplicate_code: "同一层里有重复的编号，已去重",
  category_cleared: "部分物品类别不在你家现有词汇里，已清空该限制",
  possible_duplicate: "和你家已有的名称重名",
  empty_vocabulary: "你家还没有物品类别，所以格子暂时不限制类别",
};

type Filter = "all" | StructureProposalStatus;

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "pending", label: "待处理" },
  { value: "accepted", label: "已接受" },
  { value: "rejected", label: "已拒绝" },
];

function countNodes(row: StructureProposalRow): {
  rooms: number;
  units: number;
  sections: number;
  slots: number;
} {
  let units = 0;
  let sections = 0;
  let slots = 0;
  for (const room of row.proposal.rooms) {
    units += room.units.length;
    for (const unit of room.units) {
      sections += unit.sections.length;
      for (const section of unit.sections) {
        slots += section.slots.length;
      }
    }
  }
  return { rooms: row.proposal.rooms.length, units, sections, slots };
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  // ISO timestamp from the server; render the wall-clock as-is. Avoid
  // `toLocaleString` because SSR and the browser differ, which causes
  // hydration mismatches.
  return iso.replace("T", " ").slice(0, 16);
}

function WarningList({ warnings }: { warnings: ProposalWarning[] }) {
  if (warnings.length === 0) return null;
  return (
    <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-amber-800">
      {warnings.map((w, i) => (
        <li key={i}>
          {WARNING_LABEL[w.kind] ?? w.kind}
          {w.path ? <span className="text-amber-700">（{w.path}）</span> : null}
        </li>
      ))}
    </ul>
  );
}

function ProposalTree({ row }: { row: StructureProposalRow }) {
  return (
    <ul className="mt-3 space-y-1.5 pl-4 text-sm text-ink-700">
      {row.proposal.rooms.map((room) => (
        <li key={room.name}>
          <span className="font-medium text-ink-900">{room.name}</span>
          <span className="ml-1 text-xs text-ink-500">
            ({room.room_type})
          </span>
          <ul className="mt-1 space-y-1 pl-4">
            {room.units.map((unit) => (
              <li key={unit.name}>
                · {unit.name}
                <span className="ml-1 text-xs text-ink-500">
                  ({unit.unit_type})
                </span>
                <ul className="mt-1 space-y-0.5 pl-4 text-xs text-ink-600">
                  {unit.sections.map((section) => (
                    <li key={section.name}>
                      {section.name}
                      <span className="ml-1 text-ink-500">
                        ({section.section_type})
                      </span>
                      {section.slots.length > 0 ? (
                        <span className="ml-1.5">
                          {section.slots.map((s) => s.code).join("、")}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

function ProposalRow({
  row,
  session,
}: {
  row: StructureProposalRow;
  session: ApiSession;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejectNote, setRejectNote] = useState("");
  const [expanded, setExpanded] = useState(false);

  const counts = useMemo(() => countNodes(row), [row]);
  const status = row.status as StructureProposalStatus;

  async function act(kind: "accept" | "reject") {
    setBusy(true);
    setError(null);
    try {
      if (kind === "accept") {
        await api.acceptProposal(row.id, {}, session);
      } else {
        await api.rejectProposal(
          row.id,
          { note: rejectNote.trim() || null },
          session,
        );
      }
      router.refresh();
    } catch (err) {
      setError(err instanceof APIError ? err.message : "操作失败，请重试。");
      setBusy(false);
    }
  }

  return (
    <article className="card p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="badge">{SOURCE_LABEL[row.source]}</span>
        <span
          className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${STATUS_TONE[status]}`}
        >
          {STATUS_LABEL[status]}
        </span>
        <span className="text-xs text-ink-500">{formatTime(row.created_at)}</span>
        <span className="ml-auto text-xs text-ink-500">
          {counts.rooms} 房间 · {counts.units} 柜 · {counts.sections} 层 · {counts.slots} 格
        </span>
      </div>

      {row.description ? (
        <p className="mt-2 text-sm text-ink-700">
          <span className="text-xs text-ink-500">输入：</span>
          {row.description}
        </p>
      ) : null}

      {row.proposal.rationale ? (
        <p className="mt-2 text-sm text-ink-700">
          <span className="text-xs text-ink-500">理由：</span>
          {row.proposal.rationale}
        </p>
      ) : null}

      <WarningList warnings={row.warnings} />

      {status === "rejected" && row.rejection_note ? (
        <p className="mt-2 text-sm text-ink-600">
          <span className="text-xs text-ink-500">拒绝理由：</span>
          {row.rejection_note}
        </p>
      ) : null}

      <details
        className="mt-3 text-sm"
        open={expanded}
        onToggle={(e) => setExpanded((e.target as HTMLDetailsElement).open)}
      >
        <summary className="cursor-pointer text-brand-600 hover:text-brand-700">
          {expanded ? "收起预览" : "查看提议结构"}
        </summary>
        <ProposalTree row={row} />
      </details>

      {status === "pending" ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => void act("accept")}
          >
            {busy ? <Spinner className="h-4 w-4" /> : null}
            接受并创建
          </button>
          <details className="ml-auto">
            <summary className="cursor-pointer text-sm text-ink-500 hover:text-ink-700">
              拒绝
            </summary>
            <div className="mt-2 space-y-2">
              <input
                className="input w-full"
                placeholder="可选：为什么这个提议不合适"
                value={rejectNote}
                onChange={(e) => setRejectNote(e.target.value)}
                maxLength={512}
              />
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={() => void act("reject")}
              >
                确认拒绝
              </button>
            </div>
          </details>
        </div>
      ) : null}

      {status === "accepted" ? (
        <div className="mt-3 text-sm">
          <Link href="/home/storage" className="text-brand-600 hover:text-brand-700">
            已创建，去查看收纳空间 →
          </Link>
          {row.accepted_at ? (
            <span className="ml-3 text-xs text-ink-500">
              创建于 {formatTime(row.accepted_at)}
            </span>
          ) : null}
        </div>
      ) : null}

      {status === "rejected" && row.rejected_at ? (
        <div className="mt-3 text-xs text-ink-500">
          拒绝于 {formatTime(row.rejected_at)}
        </div>
      ) : null}

      {error ? <p className="mt-2 text-sm text-red-600">{error}</p> : null}
    </article>
  );
}

export function ProposalsList({ rows }: { rows: StructureProposalRow[] }) {
  // Client-side: read once when the page mounts. Server-rendered parent is
  // responsible for the same data, so a brief 401 → re-fetch path under
  // `api.ts`'s retry will cover a session that aged out between SSR and
  // hydration.
  const session = getSession();
  const [filter, setFilter] = useState<Filter>("all");

  const visible = useMemo(
    () => (filter === "all" ? rows : rows.filter((r) => r.status === filter)),
    [rows, filter],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setFilter(f.value)}
            className={
              filter === f.value
                ? "rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-xs font-medium text-brand-700"
                : "rounded-full border border-ink-200 bg-white px-3 py-1 text-xs text-ink-600 hover:border-ink-300"
            }
          >
            {f.label}
            {f.value !== "all" ? ` · ${rows.filter((r) => r.status === f.value).length}` : ` · ${rows.length}`}
          </button>
        ))}
      </div>

      <div className="space-y-3">
        {visible.length === 0 ? (
          <p className="text-sm text-ink-500">该筛选下没有提议。</p>
        ) : (
          visible.map((row) => {
            if (!session) return null;
            return <ProposalRow key={row.id} row={row} session={session} />;
          })
        )}
      </div>
    </div>
  );
}
