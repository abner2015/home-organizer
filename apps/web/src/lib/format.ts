import type { PlacementRefView, SlotRef } from "./types";

// Two shapes reach the UI for "where an item is":
//   - SlotRef        — the search/candidate shape, with room/unit/section parts.
//   - PlacementRefView — the item read API's shape, already resolved to a path.
export type SlotLocation = SlotRef | PlacementRefView;

export function formatDateTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

export function formatRelative(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diffMs = Date.now() - d.getTime();
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  return formatDateTime(iso);
}

// The backend builds `full_path` from the slot's Chinese `label`
// ("客厅/客厅装饰柜/左玻璃柜第1层") — `code` is ASCII (`L1` / `L1S1`) and is
// machine identity only, never shown to the user.
export function formatSlotPath(loc?: SlotLocation | null): string {
  if (!loc) return "未放置";
  if ("full_path" in loc && loc.full_path) return loc.full_path;
  if ("slot_path" in loc && loc.slot_path) return loc.slot_path;
  if ("room_name" in loc) {
    return [loc.room_name, loc.unit_name, loc.section_name, loc.label]
      .filter(Boolean)
      .join(" / ");
  }
  return "未放置";
}

// The backend's `Item.category` is a controlled English vocabulary
// (app/db/enums.py); these are the labels the UI shows for it.
export const CATEGORY_LABEL: Record<string, string> = {
  appliance: "家电",
  books: "书籍",
  clothes: "衣物",
  decor: "装饰",
  electronic: "数码",
  food: "食品",
  misc: "杂物",
  medicine: "药品",
  utensil: "餐具",
};

export function formatCategory(category?: string | null): string {
  if (!category) return "未分类";
  return CATEGORY_LABEL[category] ?? category;
}

export const SIZE_LABEL: Record<string, string> = {
  small: "小",
  medium: "中",
  large: "大",
};

export function formatSize(size?: string | null): string {
  if (!size) return "未指定";
  return SIZE_LABEL[size] ?? size;
}

export function formatConfidence(c?: number): string {
  if (c === undefined || c === null) return "—";
  return `${Math.round(c * 100)}%`;
}

export function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}

export function clsx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
