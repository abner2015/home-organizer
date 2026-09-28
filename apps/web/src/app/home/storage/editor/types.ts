// Editor-local types — shared by every component in this directory so the
// shape of the local tree (mirrors `SpaceTree` from `@/lib/types`) and the
// optimistic-update helpers stay in one place.
//
// The `SpaceTree` shape is reused as-is: drag-drop reorders nodes within the
// same field on the existing objects, so the network view models are
// structurally compatible with what the editor mutates.

import type {
  Room,
  RoomType,
  SectionType,
  StorageSection,
  StorageSlot,
  StorageUnit,
  UnitType,
  UUID,
} from "@/lib/types";

// "Node kind" used by draggable / droppable data attributes. Distinguishes
// `Room` (top-level, no parent drag target) from the three sortable levels.
export type NodeKind = "room" | "unit" | "section" | "slot";

// Lookup maps used to resolve `id → kind` from anywhere in the tree without
// having to walk it. Built once per `tree` change.
export type NodeIndex = {
  room: Map<UUID, Room & { units: StorageUnit[] }>;
  unit: Map<UUID, StorageUnit>;
  section: Map<UUID, StorageSection>;
  slot: Map<UUID, StorageSlot>;
};

// All scalar fields the editor lets the user change. ``Room`` is missing
// `label` / `capacity_hint` / `allowed_categories` because those belong to
// slots only.
export type FieldChange =
  | { kind: "room"; id: UUID; field: "name" | "room_type"; value: string }
  | { kind: "unit"; id: UUID; field: "name" | "unit_type"; value: string }
  | {
      kind: "section";
      id: UUID;
      field: "name" | "section_type";
      value: string;
    }
  | {
      kind: "slot";
      id: UUID;
      field: "code" | "label" | "capacity_hint" | "allowed_categories";
      value: string | string[];
    };

// Returned from `useEditor` so callers can dispatch typed mutations without
// having to know which API call backs each one. The container owns the
// optimistic-update + rollback machinery; leaves just call into it.
export type EditorApi = {
  // rename / type-change / slot-code-rename etc.
  applyField(change: FieldChange): Promise<void>;
  // Create a child under the given parent. The new row is appended at the
  // end of its sibling list locally and replaced with the server response on
  // success.
  createChild(input: CreateChildInput): Promise<void>;
  // Delete a node; the service refuses if children / placements exist (409),
  // which surfaces as a red toast.
  deleteNode(kind: NodeKind, id: UUID): Promise<void>;
  // Reparent a node into a different parent of the kind above it.
  moveNode(input: MoveInput): Promise<void>;
  // Bulk: delete every selected id. Failures are reported per-id.
  bulkDelete(ids: UUID[]): Promise<void>;
  // Bulk: move every selected id into the same target parent.
  bulkMove(input: MoveInput, ids: UUID[]): Promise<void>;
};

export type CreateChildInput =
  | {
      kind: "unit";
      roomId: UUID;
      name: string;
      unit_type: UnitType;
    }
  | {
      kind: "section";
      unitId: UUID;
      name: string;
      section_type: SectionType;
    }
  | {
      kind: "slot";
      sectionId: UUID;
      code: string;
      label?: string;
    };

export type MoveInput =
  | { kind: "unit"; id: UUID; newRoomId: UUID }
  | { kind: "section"; id: UUID; newUnitId: UUID }
  | { kind: "slot"; id: UUID; newSectionId: UUID; newCode: string };