"use client";

import Link from "next/link";
import { clsx } from "@/lib/format";
import type { SpaceTree, StorageSlot, UUID } from "@/lib/types";

/**
 * The display path of a slot, built client-side.
 *
 * The space tree's leaf (`StorageSlot`) carries only its own `label`/`code` —
 * unlike `get_storage_slots`'s `full_path`, which the server assembles from
 * four tables. The parts are all in the tree, so the path is joined here rather
 * than fetched again per slot.
 */
export function slotDisplayPath(
  roomName: string,
  unitName: string,
  sectionName: string,
  slot: StorageSlot,
): string {
  return [roomName, unitName, sectionName, slot.label ?? slot.code]
    .filter(Boolean)
    .join(" / ");
}

interface SlotPickerProps {
  tree: SpaceTree | null;
  onPick: (slotId: UUID, path: string) => void;
  busy?: boolean;
  /** The item's current slot: marked, and never re-posted. */
  currentSlotId?: UUID | null;
  /** Shown under the current-slot note, e.g. "放入后会自动从原位置移出". */
  currentSlotNote?: string;
}

/**
 * Room → cabinet → layer → slot, as a picker.
 *
 * Deliberately not `RecommendationTree`: that one renders `CandidateView[]`
 * (it reads `.is_recommended` and has no click callback). Only the nesting
 * idea is shared.
 */
export function SlotPicker({
  tree,
  onPick,
  busy,
  currentSlotId,
  currentSlotNote,
}: SlotPickerProps) {
  const rooms = (tree?.rooms ?? []).filter((r) =>
    r.units.some((u) => u.sections.some((s) => s.slots.length > 0)),
  );

  if (rooms.length === 0) {
    return (
      <div className="rounded-lg border border-ink-100 bg-ink-50 p-4 text-sm text-ink-600">
        还没有可用的收纳位置，先搭一个柜子才放得进去。
        <Link href="/home/setup" className="ml-1 font-medium text-brand-600 hover:underline">
          去搭建我的家
        </Link>
      </div>
    );
  }

  return (
    <div className="max-h-80 space-y-3 overflow-y-auto rounded-lg border border-ink-100 p-3">
      {currentSlotId ? (
        <p className="rounded-md bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">
          {currentSlotNote ?? "该物品已经在别处，放入后会自动从原位置移出。"}
        </p>
      ) : null}

      {rooms.map((room) => (
        <div key={room.id}>
          <p className="px-1 text-xs font-semibold uppercase tracking-wide text-ink-400">
            {room.name}
          </p>
          {room.units.map((unit) => {
            const sections = unit.sections.filter((s) => s.slots.length > 0);
            if (sections.length === 0) return null;
            return (
              <div key={unit.id} className="mt-1.5">
                <p className="px-1 text-xs text-ink-500">{unit.name}</p>
                {sections.map((section) => (
                  <div key={section.id} className="mt-1 px-1">
                    <p className="text-[11px] text-ink-400">{section.name}</p>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {section.slots.map((slot) => {
                        const path = slotDisplayPath(
                          room.name,
                          unit.name,
                          section.name,
                          slot,
                        );
                        const isCurrent = slot.id === currentSlotId;
                        return (
                          <button
                            key={slot.id}
                            type="button"
                            title={path}
                            disabled={busy || isCurrent}
                            onClick={() => onPick(slot.id, path)}
                            className={clsx(
                              "rounded-lg border px-2 py-1 text-xs transition-colors",
                              isCurrent
                                ? "border-brand-200 bg-brand-50 text-brand-700"
                                : "border-ink-200 text-ink-700 hover:border-brand-300 hover:bg-brand-50",
                              busy && "opacity-50",
                            )}
                          >
                            {slot.label ?? slot.code}
                            <span className="ml-1 text-[10px] text-ink-400">
                              {isCurrent ? "当前" : `${slot.active_count ?? 0} 件`}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
