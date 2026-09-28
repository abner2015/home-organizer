"use client";

// Inline <select> for the type field (room_type / unit_type / section_type).
// Kept dead-simple: a single change handler that calls ``onCommit`` with the
// new value. The container owns the optimistic-update + API call.

import type {
  RoomType,
  SectionType,
  UnitType,
} from "@/lib/types";

type Kind = "room" | "unit" | "section";

const ROOM_TYPES: { value: RoomType; label: string }[] = [
  { value: "bedroom", label: "卧室" },
  { value: "kitchen", label: "厨房" },
  { value: "bathroom", label: "卫生间" },
  { value: "study", label: "书房" },
  { value: "living", label: "客厅" },
  { value: "storage", label: "储物间" },
  { value: "other", label: "其他" },
];

const UNIT_TYPES: { value: UnitType; label: string }[] = [
  { value: "cabinet", label: "柜子" },
  { value: "shelf", label: "架子" },
  { value: "drawer_cabinet", label: "抽屉柜" },
  { value: "box", label: "箱子" },
  { value: "other", label: "其他" },
];

const SECTION_TYPES: { value: SectionType; label: string }[] = [
  { value: "layer", label: "层" },
  { value: "drawer", label: "抽屉" },
  { value: "box", label: "箱格" },
  { value: "compartment", label: "隔间" },
  { value: "other", label: "其他" },
];

function options(kind: Kind) {
  if (kind === "room") return ROOM_TYPES;
  if (kind === "unit") return UNIT_TYPES;
  return SECTION_TYPES;
}

export type EditableTypeProps = {
  kind: Kind;
  value: RoomType | UnitType | SectionType;
  onCommit: (next: RoomType | UnitType | SectionType) => Promise<void> | void;
  disabled?: boolean;
};

export function EditableType({
  kind,
  value,
  onCommit,
  disabled,
}: EditableTypeProps) {
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={async (e) => {
        await onCommit(e.target.value as never);
      }}
      // Compact size; mirrors the existing ``.input`` look so it doesn't
      // disrupt the layout of inline-edit rows.
      className="input h-7 px-2 text-xs"
    >
      {options(kind).map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}