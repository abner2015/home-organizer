// Chinese labels for the structure enums, shared by the two builders on
// `/home/setup`. The values must stay identical to `app/db/enums.py` — they are
// posted straight back to the write API, which rejects anything else with a 422.
import type { RoomType, SectionType, UnitType } from "@/lib/types";

export const ROOM_TYPE_OPTIONS: Array<{ value: RoomType; label: string }> = [
  { value: "bedroom", label: "卧室" },
  { value: "kitchen", label: "厨房" },
  { value: "living", label: "客厅" },
  { value: "bathroom", label: "卫生间" },
  { value: "study", label: "书房" },
  { value: "storage", label: "储藏间" },
  { value: "other", label: "其他" },
];

export const UNIT_TYPE_OPTIONS: Array<{ value: UnitType; label: string }> = [
  { value: "cabinet", label: "柜子" },
  { value: "drawer_cabinet", label: "抽屉柜" },
  { value: "shelf", label: "架子" },
  { value: "box", label: "收纳箱" },
  { value: "other", label: "其他" },
];

export const SECTION_TYPE_OPTIONS: Array<{ value: SectionType; label: string }> = [
  { value: "layer", label: "层" },
  { value: "drawer", label: "抽屉" },
  { value: "box", label: "盒" },
  { value: "compartment", label: "分格" },
  { value: "other", label: "其他" },
];

export function labelOf(
  options: Array<{ value: string; label: string }>,
  value: string,
): string {
  return options.find((o) => o.value === value)?.label ?? value;
}
