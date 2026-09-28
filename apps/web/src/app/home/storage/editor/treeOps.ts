// Pure tree-mutation helpers used by the editor.
//
// Each helper takes the current tree plus a change spec and returns a brand
// new tree (shallow copies down the path it touches, deep copies where it
// inserts / removes). The editor treats this as an immutable store so React
// re-renders stay predictable.

import type {
  Room,
  RoomType,
  SectionType,
  SpaceTree,
  StorageSection,
  StorageSlot,
  StorageUnit,
  UnitType,
  UUID,
} from "@/lib/types";

// The local editor types (``FieldChange`` etc.) live in ``./types.ts``. We
// re-export ``FieldChange`` and ``MoveInput`` here so the editor only needs
// one import to get both the operations and the parameter shapes.
export type { FieldChange, MoveInput } from "./types";

import type { FieldChange, NodeKind, NodeIndex } from "./types";

// Deep-clone the entire tree. Used as the baseline for any mutation so a
// single "rollback" can swap the previous reference back in.
export function cloneTree(tree: SpaceTree): SpaceTree {
  return {
    home: { ...tree.home },
    rooms: tree.rooms.map((r) => ({
      ...r,
      units: (r.units ?? []).map((u) => ({
        ...u,
        sections: (u.sections ?? []).map((s) => ({
          ...s,
          slots: (s.slots ?? []).map((sl) => ({ ...sl })),
        })),
      })),
    })),
  };
}

export function buildIndex(tree: SpaceTree): NodeIndex {
  const room = new Map<UUID, Room & { units: StorageUnit[] }>();
  const unit = new Map<UUID, StorageUnit>();
  const section = new Map<UUID, StorageSection>();
  const slot = new Map<UUID, StorageSlot>();
  for (const r of tree.rooms) {
    room.set(r.id, r);
    for (const u of r.units ?? []) {
      unit.set(u.id, u);
      for (const s of u.sections ?? []) {
        section.set(s.id, s);
        for (const sl of s.slots ?? []) {
          slot.set(sl.id, sl);
        }
      }
    }
  }
  return { room, unit, section, slot };
}

// Apply a single field change. The change has to mention the kind so the
// right branch runs (and a mistyped change fails to type-check).
export function applyField(tree: SpaceTree, change: FieldChange): SpaceTree {
  const next = cloneTree(tree);
  switch (change.kind) {
    case "room": {
      const idx = next.rooms.findIndex((r) => r.id === change.id);
      if (idx < 0) return next;
      const r = { ...next.rooms[idx] };
      if (change.field === "room_type") {
        r.room_type = change.value as RoomType;
      } else {
        r.name = change.value;
      }
      next.rooms[idx] = r;
      return next;
    }
    case "unit": {
      const [roomIdx, unitIdx] = locateRoomAndUnit(next, change.id);
      if (roomIdx < 0 || unitIdx < 0) return next;
      const units = [...next.rooms[roomIdx].units];
      const u = { ...units[unitIdx] };
      if (change.field === "unit_type") {
        u.unit_type = change.value as UnitType;
      } else {
        u.name = change.value;
      }
      units[unitIdx] = u;
      next.rooms[roomIdx] = { ...next.rooms[roomIdx], units };
      return next;
    }
    case "section": {
      const [roomIdx, unitIdx, sectionIdx] = locateRoomUnitSection(
        next,
        change.id,
      );
      if (roomIdx < 0 || unitIdx < 0 || sectionIdx < 0) return next;
      const sections = [...next.rooms[roomIdx].units[unitIdx].sections];
      const s = { ...sections[sectionIdx] };
      if (change.field === "section_type") {
        s.section_type = change.value as SectionType;
      } else {
        s.name = change.value;
      }
      sections[sectionIdx] = s;
      const unit = {
        ...next.rooms[roomIdx].units[unitIdx],
        sections,
      };
      const units = [...next.rooms[roomIdx].units];
      units[unitIdx] = unit;
      next.rooms[roomIdx] = { ...next.rooms[roomIdx], units };
      return next;
    }
    case "slot": {
      const [roomIdx, unitIdx, sectionIdx, slotIdx] = locateSlot(next,
        change.id);
      if (
        roomIdx < 0 ||
        unitIdx < 0 ||
        sectionIdx < 0 ||
        slotIdx < 0
      )
        return next;
      const slots = [...next.rooms[roomIdx].units[unitIdx].sections[sectionIdx].slots];
      const sl = { ...slots[slotIdx] };
      if (change.field === "code") sl.code = change.value as string;
      else if (change.field === "label") sl.label = change.value as string;
      else if (change.field === "capacity_hint")
        sl.capacity_hint = change.value as string;
      else sl.allowed_categories = change.value as string[];
      slots[slotIdx] = sl;
      const section = {
        ...next.rooms[roomIdx].units[unitIdx].sections[sectionIdx],
        slots,
      };
      const sections = [...next.rooms[roomIdx].units[unitIdx].sections];
      sections[sectionIdx] = section;
      const unit = {
        ...next.rooms[roomIdx].units[unitIdx],
        sections,
      };
      const units = [...next.rooms[roomIdx].units];
      units[unitIdx] = unit;
      next.rooms[roomIdx] = { ...next.rooms[roomIdx], units };
      return next;
    }
  }
}

export function deleteNode(
  tree: SpaceTree,
  kind: NodeKind,
  id: UUID,
): SpaceTree {
  const next = cloneTree(tree);
  switch (kind) {
    case "room": {
      next.rooms = next.rooms.filter((r) => r.id !== id);
      return next;
    }
    case "unit": {
      const [roomIdx] = locateRoomAndUnit(next, id);
      if (roomIdx < 0) return next;
      next.rooms[roomIdx] = {
        ...next.rooms[roomIdx],
        units: next.rooms[roomIdx].units.filter((u) => u.id !== id),
      };
      return next;
    }
    case "section": {
      const [roomIdx, unitIdx] = locateRoomUnitSection(next, id);
      if (roomIdx < 0 || unitIdx < 0) return next;
      const u = next.rooms[roomIdx].units[unitIdx];
      const updated = {
        ...u,
        sections: u.sections.filter((s) => s.id !== id),
      };
      const units = [...next.rooms[roomIdx].units];
      units[unitIdx] = updated;
      next.rooms[roomIdx] = { ...next.rooms[roomIdx], units };
      return next;
    }
    case "slot": {
      const [roomIdx, unitIdx, sectionIdx] = locateSlot(next, id);
      if (roomIdx < 0 || unitIdx < 0 || sectionIdx < 0) return next;
      const s = next.rooms[roomIdx].units[unitIdx].sections[sectionIdx];
      const updated = {
        ...s,
        slots: s.slots.filter((sl) => sl.id !== id),
      };
      const sections = [...next.rooms[roomIdx].units[unitIdx].sections];
      sections[sectionIdx] = updated;
      const unit = {
        ...next.rooms[roomIdx].units[unitIdx],
        sections,
      };
      const units = [...next.rooms[roomIdx].units];
      units[unitIdx] = unit;
      next.rooms[roomIdx] = { ...next.rooms[roomIdx], units };
      return next;
    }
  }
}

// Add a freshly-created child. Caller supplies the row returned by the API
// (with id, sort_order, etc.) and the location triple. Replaces any
// optimistic placeholder (with the same id) so the server-side row wins.
export function insertChild(
  tree: SpaceTree,
  child:
    | { kind: "unit"; row: StorageUnit; roomId: UUID }
    | { kind: "section"; row: StorageSection; unitId: UUID }
    | { kind: "slot"; row: StorageSlot; sectionId: UUID },
): SpaceTree {
  const next = cloneTree(tree);
  if (child.kind === "unit") {
    const idx = next.rooms.findIndex((r) => r.id === child.roomId);
    if (idx < 0) return next;
    const room = next.rooms[idx];
    const units = [...(room.units ?? []), child.row];
    next.rooms[idx] = { ...room, units };
    return next;
  }
  if (child.kind === "section") {
    const [roomIdx, unitIdx] = locateRoomAndUnit(next, child.unitId);
    if (roomIdx < 0 || unitIdx < 0) return next;
    const u = next.rooms[roomIdx].units[unitIdx];
    const sections = [...(u.sections ?? []), child.row];
    const units = [...next.rooms[roomIdx].units];
    units[unitIdx] = { ...u, sections };
    next.rooms[roomIdx] = { ...next.rooms[roomIdx], units };
    return next;
  }
  const [roomIdx, unitIdx, sectionIdx] = locateRoomUnitSection(
    next,
    child.sectionId,
  );
  if (roomIdx < 0 || unitIdx < 0 || sectionIdx < 0) return next;
  const s = next.rooms[roomIdx].units[unitIdx].sections[sectionIdx];
  const slots = [...(s.slots ?? []), child.row];
  const sections = [...next.rooms[roomIdx].units[unitIdx].sections];
  sections[sectionIdx] = { ...s, slots };
  const unit = {
    ...next.rooms[roomIdx].units[unitIdx],
    sections,
  };
  const units = [...next.rooms[roomIdx].units];
  units[unitIdx] = unit;
  next.rooms[roomIdx] = { ...next.rooms[roomIdx], units };
  return next;
}

// Reparent a node. ``srcTriple`` is the *current* (roomIdx, unitIdx, sectionIdx)
// for slots/sections; ``dstTriple`` is the new parent triple.
export function reparent(
  tree: SpaceTree,
  kind: "unit" | "section" | "slot",
  src: { id: UUID },
  newParent: { roomId?: UUID; unitId?: UUID; sectionId?: UUID },
): SpaceTree {
  const next = cloneTree(tree);
  if (kind === "unit") {
    const idx = next.rooms.findIndex((r) => r.id === newParent.roomId);
    if (idx < 0) return next;
    // Find and remove the unit from its current room.
    let removed: StorageUnit | null = null;
    for (let r = 0; r < next.rooms.length; r++) {
      const u = next.rooms[r].units?.find((x) => x.id === src.id);
      if (u) {
        removed = u;
        next.rooms[r] = {
          ...next.rooms[r],
          units: next.rooms[r].units.filter((x) => x.id !== src.id),
        };
        break;
      }
    }
    if (!removed) return next;
    // Update its room_id and re-insert.
    const moved = { ...removed, room_id: newParent.roomId! };
    next.rooms[idx] = {
      ...next.rooms[idx],
      units: [...(next.rooms[idx].units ?? []), moved],
    };
    return next;
  }
  if (kind === "section") {
    let removed: StorageSection | null = null;
    let srcRoomIdx = -1;
    let srcUnitIdx = -1;
    outer: for (let r = 0; r < next.rooms.length; r++) {
      for (let u = 0; u < (next.rooms[r].units ?? []).length; u++) {
        const s = next.rooms[r].units[u].sections?.find(
          (x) => x.id === src.id,
        );
        if (s) {
          removed = s;
          srcRoomIdx = r;
          srcUnitIdx = u;
          break outer;
        }
      }
    }
    if (!removed || srcRoomIdx < 0 || srcUnitIdx < 0) return next;
    next.rooms[srcRoomIdx].units[srcUnitIdx] = {
      ...next.rooms[srcRoomIdx].units[srcUnitIdx],
      sections: next.rooms[srcRoomIdx].units[srcUnitIdx].sections.filter(
        (s) => s.id !== src.id,
      ),
    };
    const [dstRoomIdx, dstUnitIdx] = locateRoomAndUnit(next, newParent.unitId!);
    if (dstRoomIdx < 0 || dstUnitIdx < 0) return next;
    const moved = { ...removed, unit_id: newParent.unitId! };
    const dstUnit = next.rooms[dstRoomIdx].units[dstUnitIdx];
    const updated = {
      ...dstUnit,
      sections: [...(dstUnit.sections ?? []), moved],
    };
    const units = [...next.rooms[dstRoomIdx].units];
    units[dstUnitIdx] = updated;
    next.rooms[dstRoomIdx] = { ...next.rooms[dstRoomIdx], units };
    return next;
  }
  // slot
  let removed: StorageSlot | null = null;
  let srcRoomIdx = -1;
  let srcUnitIdx = -1;
  let srcSectionIdx = -1;
  outer: for (let r = 0; r < next.rooms.length; r++) {
    for (let u = 0; u < (next.rooms[r].units ?? []).length; u++) {
      const sections = next.rooms[r].units[u].sections ?? [];
      for (let s = 0; s < sections.length; s++) {
        const sl = sections[s].slots?.find((x) => x.id === src.id);
        if (sl) {
          removed = sl;
          srcRoomIdx = r;
          srcUnitIdx = u;
          srcSectionIdx = s;
          break outer;
        }
      }
    }
  }
  if (
    !removed ||
    srcRoomIdx < 0 ||
    srcUnitIdx < 0 ||
    srcSectionIdx < 0
  )
    return next;
  next.rooms[srcRoomIdx].units[srcUnitIdx].sections[srcSectionIdx] = {
    ...next.rooms[srcRoomIdx].units[srcUnitIdx].sections[srcSectionIdx],
    slots: next.rooms[srcRoomIdx].units[srcUnitIdx].sections[
      srcSectionIdx
    ].slots.filter((sl) => sl.id !== src.id),
  };
  const [dstRoomIdx, dstUnitIdx, dstSectionIdx] = locateRoomUnitSection(
    next,
    newParent.sectionId!,
  );
  if (dstRoomIdx < 0 || dstUnitIdx < 0 || dstSectionIdx < 0) return next;
  const moved = {
    ...removed,
    section_id: newParent.sectionId!,
  };
  const dstSection = next.rooms[dstRoomIdx].units[dstUnitIdx].sections[
    dstSectionIdx
  ];
  const updated = {
    ...dstSection,
    slots: [...(dstSection.slots ?? []), moved],
  };
  const sections = [...next.rooms[dstRoomIdx].units[dstUnitIdx].sections];
  sections[dstSectionIdx] = updated;
  const unit = {
    ...next.rooms[dstRoomIdx].units[dstUnitIdx],
    sections,
  };
  const units = [...next.rooms[dstRoomIdx].units];
  units[dstUnitIdx] = unit;
  next.rooms[dstRoomIdx] = { ...next.rooms[dstRoomIdx], units };
  return next;
}

// Reorder siblings at a given level. ``orderedIds`` is the post-sort id order;
// anything missing is dropped from the list. Used by drag-end after a same-
// parent drag and by the bulk-reorder button.
export function reorderChildren(
  tree: SpaceTree,
  kind: "room" | "unit" | "section" | "slot",
  parentId: UUID,
  orderedIds: UUID[],
): SpaceTree {
  const next = cloneTree(tree);
  const lookup = new Map(orderedIds.map((id, i) => [id, i] as const));
  const sorter = (a: { id: UUID }, b: { id: UUID }) => {
    const ai = lookup.has(a.id) ? lookup.get(a.id)! : Number.MAX_SAFE_INTEGER;
    const bi = lookup.has(b.id) ? lookup.get(b.id)! : Number.MAX_SAFE_INTEGER;
    return ai - bi;
  };
  if (kind === "room") {
    next.rooms = [...next.rooms].sort(sorter);
    return next;
  }
  if (kind === "unit") {
    const idx = next.rooms.findIndex((r) => r.id === parentId);
    if (idx < 0) return next;
    next.rooms[idx] = {
      ...next.rooms[idx],
      units: [...(next.rooms[idx].units ?? [])].sort(sorter),
    };
    return next;
  }
  if (kind === "section") {
    const [roomIdx, unitIdx] = locateRoomAndUnit(next, parentId);
    if (roomIdx < 0 || unitIdx < 0) return next;
    const u = next.rooms[roomIdx].units[unitIdx];
    const units = [...next.rooms[roomIdx].units];
    units[unitIdx] = { ...u, sections: [...(u.sections ?? [])].sort(sorter) };
    next.rooms[roomIdx] = { ...next.rooms[roomIdx], units };
    return next;
  }
  const [roomIdx, unitIdx, sectionIdx] = locateRoomUnitSection(next, parentId);
  if (roomIdx < 0 || unitIdx < 0 || sectionIdx < 0) return next;
  const s = next.rooms[roomIdx].units[unitIdx].sections[sectionIdx];
  const sections = [...next.rooms[roomIdx].units[unitIdx].sections];
  sections[sectionIdx] = { ...s, slots: [...(s.slots ?? [])].sort(sorter) };
  const unit = {
    ...next.rooms[roomIdx].units[unitIdx],
    sections,
  };
  const units = [...next.rooms[roomIdx].units];
  units[unitIdx] = unit;
  next.rooms[roomIdx] = { ...next.rooms[roomIdx], units };
  return next;
}

// ----- helpers for locating (room,unit,section) tuples by id -----

function locateRoomAndUnit(
  tree: SpaceTree,
  unitId: UUID,
): [number, number] {
  for (let r = 0; r < tree.rooms.length; r++) {
    const u = tree.rooms[r].units?.findIndex((x) => x.id === unitId);
    if (typeof u === "number" && u >= 0) return [r, u];
  }
  return [-1, -1];
}

function locateRoomUnitSection(
  tree: SpaceTree,
  sectionId: UUID,
): [number, number, number] {
  for (let r = 0; r < tree.rooms.length; r++) {
    for (let u = 0; u < (tree.rooms[r].units ?? []).length; u++) {
      const s = tree.rooms[r].units[u].sections?.findIndex(
        (x) => x.id === sectionId,
      );
      if (typeof s === "number" && s >= 0) return [r, u, s];
    }
  }
  return [-1, -1, -1];
}

function locateSlot(
  tree: SpaceTree,
  slotId: UUID,
): [number, number, number, number] {
  for (let r = 0; r < tree.rooms.length; r++) {
    for (let u = 0; u < (tree.rooms[r].units ?? []).length; u++) {
      const sections = tree.rooms[r].units[u].sections ?? [];
      for (let s = 0; s < sections.length; s++) {
        const sl = sections[s].slots?.findIndex((x) => x.id === slotId);
        if (typeof sl === "number" && sl >= 0) return [r, u, s, sl];
      }
    }
  }
  return [-1, -1, -1, -1];
}