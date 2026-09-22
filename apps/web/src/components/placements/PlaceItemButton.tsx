"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, APIError, type ApiSession } from "@/lib/api";
import { Spinner } from "@/components/States";
import type { Item, SpaceTree, UUID } from "@/lib/types";
import { SlotPicker } from "./SlotPicker";

/**
 * "放到这里" — put one item into a slot, no AI involved ("反向录入").
 *
 * After a successful placement the button reports where it went and the page
 * is refreshed **without navigating**: this is the per-item half of
 * 「连续补录多件」, and a route change would lose the user's place in the list.
 *
 * The tree is fetched when the picker opens, never cached at module scope —
 * `router.refresh()` would not invalidate such a cache, so a batch page could
 * happily show "0 items placed" on a slot it had just filled.
 */
export function PlaceItemButton({
  item,
  session,
  label = "放到这里",
}: {
  item: Item;
  session: ApiSession;
  label?: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [tree, setTree] = useState<SpaceTree | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [justPlaced, setJustPlaced] = useState<string | null>(null);

  async function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    setJustPlaced(null);
    setLoading(true);
    setError(null);
    try {
      setTree(await api.getSpaceTree(session));
    } catch {
      setError("无法加载收纳结构。");
    } finally {
      setLoading(false);
    }
  }

  async function pick(slotId: UUID, path: string) {
    setBusy(true);
    setError(null);
    try {
      await api.placeItem({ item_id: item.id, slot_id: slotId }, session);
      setJustPlaced(path);
      setOpen(false);
      router.refresh();
    } catch (err) {
      setError(err instanceof APIError ? err.message : "放置失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  const current = item.current_placement;

  return (
    <div className="text-xs">
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn-secondary !px-2.5 !py-1 !text-xs"
          disabled={busy}
          onClick={() => void toggle()}
        >
          {busy ? <Spinner className="h-3.5 w-3.5" /> : null}
          {open ? "取消" : label}
        </button>
        {justPlaced && !open ? (
          <span className="truncate text-ink-500">已放入「{justPlaced}」</span>
        ) : null}
      </div>

      {open ? (
        <div className="mt-2" onClick={(e) => e.stopPropagation()}>
          {loading ? (
            <p className="text-ink-500">正在加载收纳结构…</p>
          ) : (
            <SlotPicker
              tree={tree}
              busy={busy}
              currentSlotId={current?.slot_id ?? null}
              onPick={(slotId, path) => void pick(slotId, path)}
            />
          )}
        </div>
      ) : null}

      {error ? <p className="mt-1 text-red-600">{error}</p> : null}
    </div>
  );
}
