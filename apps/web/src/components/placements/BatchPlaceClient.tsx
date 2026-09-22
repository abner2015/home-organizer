"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, APIError, type ApiSession } from "@/lib/api";
import { Loading, Spinner } from "@/components/States";
import type { Item, SpaceTree, UUID } from "@/lib/types";
import { SlotPicker } from "./SlotPicker";

type Outcome = { ok: boolean; message: string };

/**
 * "批量归位" — pick one slot, tick several items, place them all there.
 *
 * The complement to `PlaceItemButton`: that one is for the item you are
 * looking at, this one is for the shelf you are standing in front of.
 *
 * Requests are sent **sequentially**. Firing them concurrently would make a
 * per-item failure impossible to attribute (and a variable number of items
 * would hit the single active-placement invariant at once); the results are
 * reported per item so a partial batch is visible rather than silent.
 */
export function BatchPlaceClient({
  items,
  session,
}: {
  items: Item[];
  session: ApiSession;
}) {
  const router = useRouter();
  const [tree, setTree] = useState<SpaceTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [slot, setSlot] = useState<{ id: UUID; path: string } | null>(null);
  const [checked, setChecked] = useState<Set<UUID>>(new Set());
  const [results, setResults] = useState<Record<UUID, Outcome>>({});
  const [error, setError] = useState<string | null>(null);

  async function loadTree() {
    try {
      setTree(await api.getSpaceTree(session));
    } catch {
      setError("无法加载收纳结构。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadTree();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session]);

  function toggle(itemId: UUID) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  }

  async function placeAll() {
    if (!slot || checked.size === 0) return;
    setBusy(true);
    setError(null);
    setResults({});

    const done: Record<UUID, Outcome> = {};
    for (const item of items) {
      if (!checked.has(item.id)) continue;
      try {
        await api.placeItem({ item_id: item.id, slot_id: slot.id }, session);
        done[item.id] = { ok: true, message: "已放入" };
      } catch (err) {
        done[item.id] = {
          ok: false,
          message: err instanceof APIError ? err.message : "放置失败",
        };
      }
      setResults({ ...done });
    }

    setBusy(false);
    setChecked(new Set());
    // Occupancy changed on the slot just used, so the picker's counts are
    // stale; re-read rather than leaving the user with a wrong number.
    await loadTree();
    router.refresh();
  }

  if (loading) {
    return (
      <div className="card p-8">
        <Loading label="正在加载收纳结构…" />
      </div>
    );
  }

  const placedCount = Object.values(results).filter((r) => r.ok).length;

  return (
    <div className="space-y-4">
      <section className="card space-y-3 p-5">
        <div>
          <h2 className="text-sm font-semibold text-ink-800">1. 选择要放的位置</h2>
          <p className="mt-0.5 text-xs text-ink-500">
            {slot ? (
              <>
                已选：<span className="font-medium text-brand-700">{slot.path}</span>
              </>
            ) : (
              "点一个格子，下面勾选要放进这里的物品。"
            )}
          </p>
        </div>
        <SlotPicker
          tree={tree}
          busy={busy}
          currentSlotId={slot?.id ?? null}
          currentSlotNote="把多件物品放进这里；已经在别处的会被自动从原位置移出。"
          onPick={(slotId, path) => setSlot({ id: slotId, path })}
        />
      </section>

      <section className="card p-5">
        <h2 className="text-sm font-semibold text-ink-800">
          2. 勾选物品（已选 {checked.size} 件）
        </h2>
        {items.length === 0 ? (
          <p className="mt-3 text-sm text-ink-500">
            还没有物品。
            <Link href="/items/new" className="ml-1 font-medium text-brand-600 hover:underline">
              先添加一件
            </Link>
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-ink-100">
            {items.map((item) => {
              const outcome = results[item.id];
              const isPlaced = Boolean(item.current_placement);
              return (
                <li key={item.id} className="flex items-center gap-3 py-2">
                  <input
                    id={`batch-${item.id}`}
                    type="checkbox"
                    className="h-4 w-4 shrink-0"
                    checked={checked.has(item.id)}
                    disabled={busy}
                    onChange={() => toggle(item.id)}
                  />
                  <label
                    htmlFor={`batch-${item.id}`}
                    className="min-w-0 flex-1 cursor-pointer"
                  >
                    <span className="block truncate text-sm text-ink-800">
                      {item.name}
                      {isPlaced ? (
                        <span className="ml-2 text-xs text-amber-700">
                          将被自动移出「{item.current_placement?.slot_path}」
                        </span>
                      ) : null}
                    </span>
                    <span className="block truncate text-xs text-ink-500">
                      📍 {item.current_placement?.slot_path ?? "未放置"}
                    </span>
                  </label>
                  {outcome ? (
                    <span
                      className={
                        outcome.ok
                          ? "shrink-0 text-xs text-emerald-600"
                          : "shrink-0 text-xs text-red-600"
                      }
                    >
                      {outcome.message}
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="btn-primary"
          disabled={busy || !slot || checked.size === 0}
          onClick={() => void placeAll()}
        >
          {busy ? <Spinner className="h-4 w-4" /> : null}
          放入所选位置
        </button>
        {placedCount > 0 ? (
          <span className="text-sm text-ink-600">已放入 {placedCount} 件</span>
        ) : null}
        {error ? <span className="text-sm text-red-600">{error}</span> : null}
      </div>
    </div>
  );
}
