"use client";

// Sticky bar that appears at the top of the editor once the user has
// selected one or more checkboxes. Lets them:
//
// - clear the selection (×)
// - bulk-delete (two-step confirm like ConfirmDelete)
// - bulk-move to one target parent (a `<select>` populated from the same
//   tree the editor is showing)
//
// Both actions run in parallel (``Promise.allSettled``) so a slow network
// doesn't serialize them, and partial failures are surfaced in the toast
// rather than blowing the whole batch up.

import { useState } from "react";
import type { UUID } from "@/lib/types";
import { ConfirmDelete } from "./ConfirmDelete";

export type BulkSelection = {
  rooms: Set<UUID>;
  units: Set<UUID>;
  sections: Set<UUID>;
  slots: Set<UUID>;
};

// Flattened view of a single storage hierarchy — the bar's caller (the
// editor's local tree) hands us rows that look like ``SpaceTree.rooms``.
type RoomLike = {
  id: UUID;
  name: string;
  units?: Array<{
    id: UUID;
    name: string;
    sections?: Array<{ id: UUID; name: string }>;
  }>;
};

export type BulkActionBarProps = {
  selection: BulkSelection;
  rooms: RoomLike[];
  onClear: () => void;
  onBulkDelete: (ids: UUID[]) => Promise<void>;
  onBulkMove: (
    input:
        | { kind: "unit"; newRoomId: UUID; ids: UUID[] }
        | { kind: "section"; newUnitId: UUID; ids: UUID[] }
        | { kind: "slot"; newSectionId: UUID; newCode: string; ids: UUID[] },
  ) => Promise<void>;
};

export function BulkActionBar({
  selection,
  rooms,
  onClear,
  onBulkDelete,
  onBulkMove,
}: BulkActionBarProps) {
  // Figure out which level is dominant (i.e. which kind has any items
  // selected). Mixing kinds in one bulk op would mean four different API
  // calls; the bar is "the simplest one wins" — if you select units AND
  // rooms, only units are shown in the move dropdown.
  const total =
    selection.rooms.size +
    selection.units.size +
    selection.sections.size +
    selection.slots.size;

  const kind: "room" | "unit" | "section" | "slot" | null =
    selection.units.size > 0
      ? "unit"
      : selection.sections.size > 0
        ? "section"
        : selection.slots.size > 0
          ? "slot"
          : selection.rooms.size > 0
            ? "room"
            : null;

  const [moveTarget, setMoveTarget] = useState<string>("");
  const [slotCode, setSlotCode] = useState<string>("");

  if (total === 0 || kind === null) return null;

  const ids = Array.from(
    kind === "room"
      ? selection.rooms
      : kind === "unit"
        ? selection.units
        : kind === "section"
          ? selection.sections
          : selection.slots,
  );

  async function handleMove() {
    if (!moveTarget || !kind) return;
    if (kind === "room") return; // rooms can't move
    if (kind === "unit") {
      await onBulkMove({
        kind: "unit",
        newRoomId: moveTarget,
        ids,
      });
    } else if (kind === "section") {
      await onBulkMove({
        kind: "section",
        newUnitId: moveTarget,
        ids,
      });
    } else {
      await onBulkMove({
        kind: "slot",
        newSectionId: moveTarget,
        newCode: slotCode.trim(),
        ids,
      });
    }
  }

  return (
    <div className="card flex items-center gap-3 border-brand-200 bg-brand-50 px-3 py-2 text-sm">
      <span className="font-semibold text-ink-800">已选 {total} 项</span>
      <button
        type="button"
        onClick={onClear}
        className="text-xs text-ink-500 hover:text-ink-700"
        title="清空选择"
      >
        ×
      </button>
      <span className="ml-2 h-4 w-px bg-ink-200" aria-hidden />
      <ConfirmDelete
        size="sm"
        label={`批量删除 ${ids.length} 项`}
        onConfirm={() => onBulkDelete(ids)}
      />
      {kind !== "room" && (
        <>
          <span className="h-4 w-px bg-ink-200" aria-hidden />
          <label className="flex items-center gap-2 text-xs">
            <span className="text-ink-600">移到 →</span>
            <MoveTargetSelect
              kind={kind}
              rooms={rooms}
              value={moveTarget}
              onChange={setMoveTarget}
            />
            {kind === "slot" && (
              <input
                value={slotCode}
                onChange={(e) => setSlotCode(e.target.value)}
                placeholder="新 code"
                className="input h-7 w-20 font-mono text-xs"
              />
            )}
            <button
              type="button"
              onClick={handleMove}
              disabled={!moveTarget || (kind === "slot" && !slotCode.trim())}
              className="btn-secondary h-7 px-2 text-xs disabled:opacity-50"
            >
              执行
            </button>
          </label>
        </>
      )}
    </div>
  );
}

// `<select>` is level-specific: units move to rooms, sections to units, slots
// to sections. Walk the tree to flatten the options.
function MoveTargetSelect({
  kind,
  rooms,
  value,
  onChange,
}: {
  kind: "unit" | "section" | "slot";
  rooms: RoomLike[];
  value: string;
  onChange: (next: string) => void;
}) {
  if (kind === "unit") {
    return (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="input h-7 text-xs"
      >
        <option value="">选择房间…</option>
        {rooms.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name}
          </option>
        ))}
      </select>
    );
  }
  if (kind === "section") {
    return (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="input h-7 text-xs"
      >
        <option value="">选择家具…</option>
        {rooms.flatMap((r) =>
          (r.units ?? []).map((u) => (
            <option key={u.id} value={u.id}>
              {r.name} / {u.name}
            </option>
          )),
        )}
      </select>
    );
  }
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="input h-7 text-xs"
    >
      <option value="">选择分区…</option>
      {rooms.flatMap((r) =>
        (r.units ?? []).flatMap((u) =>
          (u.sections ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {r.name} / {u.name} / {s.name}
            </option>
          )),
        ),
      )}
    </select>
  );
}