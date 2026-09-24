"use client";

// 「已被排除的位置」— P0.B.
//
// P0.6 added per-rec revoke (one click per rejection). But the user's mental
// model is "this item has been vetoed from N slots, let me undo that and see
// fresh candidates". This widget renders the rejected-recommendations list for
// an item and exposes a single "撤销全部排除" action that:
//
//   1. POSTs /api/v1/recommendations/bulk-revoke with all rejected rec ids
//      and `auto_rerun=true`.
//   2. Lets the synchronous rerun_results ride back in the same response —
//      the user does not have to click "再推荐一次" themselves.
//   3. Calls router.refresh() so the page re-fetches /items/{id} + the new
//      rec list, and the user sees the new candidates without a navigation.

import { useRouter } from "next/navigation";
import { useState } from "react";

import { APIError, type ApiSession, api } from "@/lib/api";
import type { ItemRecommendationRow } from "@/lib/types";
import { Spinner } from "@/components/States";

interface SlotPathMap {
  // chosen_slot_id → "room / unit / section / code"
  [slotId: string]: string;
}

export function RejectedRecsList({
  itemId,
  session,
  rejected,
  slotPaths,
}: {
  itemId: string;
  session: ApiSession;
  rejected: ItemRecommendationRow[];
  slotPaths: SlotPathMap;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  if (rejected.length === 0) {
    return (
      <p className="mt-3 text-sm text-ink-500">暂无被排除的位置</p>
    );
  }

  async function revokeAll() {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const ids = rejected.map((r) => r.id);
      const res = await api.bulkRevokeRecommendations(
        { recommendation_ids: ids, auto_rerun: true },
        session,
      );
      const rerunCount = res.rerun_results.filter((r) => r.state === "success").length;
      const failed = res.rerun_results.filter((r) => r.state === "failed").length;
      const revoked = res.revoked.length;
      const errorCount = res.errors.length;
      const parts: string[] = [];
      if (revoked) parts.push(`已撤销 ${revoked} 个排除`);
      if (rerunCount) parts.push(`已重新推荐 ${rerunCount} 件物品`);
      if (failed) parts.push(`${failed} 件物品暂时没有合适位置`);
      if (errorCount) parts.push(`${errorCount} 条失败`);
      setInfo(parts.length ? parts.join("，") : "已完成");
      // Re-render the page so the new candidates + the (now empty) rejected
      // list come back from the server. The widget itself stays mounted; the
      // list shrinks to empty on the next pass.
      router.refresh();
    } catch (err) {
      setError(extract(err, "批量撤销失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <ol className="space-y-2">
        {rejected.map((rec) => {
          const path =
            (rec.chosen_slot_id && slotPaths[rec.chosen_slot_id]) ||
            (rec.chosen_slot_id ? `槽位 ${rec.chosen_slot_id.slice(0, 8)}` : "—");
          return (
            <li
              key={rec.id}
              className="flex items-start justify-between rounded-lg bg-ink-50 px-3 py-2 text-sm"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-ink-800">{path}</p>
                {rec.reason ? (
                  <p className="mt-0.5 text-xs text-ink-500">
                    当时理由：{rec.reason}
                  </p>
                ) : null}
              </div>
              <span className="badge ml-2 shrink-0">已排除</span>
            </li>
          );
        })}
      </ol>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-secondary"
          onClick={revokeAll}
          disabled={busy}
        >
          {busy ? <Spinner className="h-4 w-4" /> : null}
          撤销全部排除（{rejected.length}）并重跑推荐
        </button>
        <span className="text-xs text-ink-500">
          一次性放回候选并自动重跑推荐
        </span>
      </div>
      {info ? (
        <p className="text-xs text-brand-700" role="status">
          {info}
        </p>
      ) : null}
      {error ? (
        <p className="text-xs text-red-600" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function extract(err: unknown, fallback: string): string {
  if (err instanceof APIError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}
