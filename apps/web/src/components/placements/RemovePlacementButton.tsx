"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, APIError, type ApiSession } from "@/lib/api";
import { Spinner } from "@/components/States";
import type { UUID } from "@/lib/types";

/**
 * "移出" — end a placement.
 *
 * Only offered on the item detail page, which already has the active
 * placement's id from `GET /items/{id}/placements`; the list page would need
 * another lookup per card for a button nobody reaches for there.
 *
 * The backend soft-closes the row (`removed_at`), so this stays reversible
 * history rather than a delete.
 */
export function RemovePlacementButton({
  placementId,
  session,
}: {
  placementId: UUID;
  session: ApiSession;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    if (!window.confirm("确定要把这件物品从当前位置移出吗？")) return;
    setBusy(true);
    setError(null);
    try {
      await api.unplaceItem(placementId, session);
      router.refresh();
    } catch (err) {
      setError(err instanceof APIError ? err.message : "移出失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <button
        type="button"
        className="btn-ghost !px-2.5 !py-1 !text-xs"
        disabled={busy}
        onClick={() => void remove()}
      >
        {busy ? <Spinner className="h-3.5 w-3.5" /> : null}
        移出
      </button>
      {error ? <p className="mt-1 text-xs text-red-600">{error}</p> : null}
    </div>
  );
}
