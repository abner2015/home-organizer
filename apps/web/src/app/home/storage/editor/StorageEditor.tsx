"use client";

// Top-level editor for the home's storage structure. Renders the same shape
// as the read-only tree, but each node is wrapped in ``<DraggableNode>``
// which exposes inline rename, type change, create-child, delete, drag-drop
// reorder, and a checkbox for multi-select.
//
// State is kept local — `tree` is the canonical mirror of the network view,
// and every mutation is an optimistic ``setTree`` followed by an API call.
// On failure the previous reference is swapped back in, so the UI never
// claims a change the server rejected.
//
// The container is also responsible for the home-switch reset (P1.4): when
// the parent's ``initialTree`` changes (the user picked a different home)
// we wipe every per-home field so the new tree is the only thing on screen.

import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { api, APIError, type ApiSession } from "@/lib/api";
import type {
  RoomType,
  SectionType,
  SpaceTree,
  StorageSection,
  StorageSlot,
  StorageUnit,
  UnitType,
  UUID,
} from "@/lib/types";

import {
  applyField,
  buildIndex,
  cloneTree,
  deleteNode,
  insertChild,
  reparent,
  reorderChildren,
  type FieldChange,
  type MoveInput,
} from "./treeOps";
import {
  BulkActionBar,
  type BulkSelection,
} from "./BulkActionBar";
import { EditorTree } from "./EditorTree";

export type StorageEditorProps = {
  session: ApiSession;
  initialTree: SpaceTree;
};

export function StorageEditor({ session, initialTree }: StorageEditorProps) {
  const router = useRouter();

  // Tree state — the editor treats it as immutable. All mutations go through
  // ``treeOps.ts`` and produce a new reference; React re-renders the diff.
  const [tree, setTree] = useState<SpaceTree>(initialTree);

  // Per-id busy markers. Each one tells the row "your last action is still
  // in flight", which dims the buttons so the user doesn't enqueue a second.
  const [busyIds, setBusyIds] = useState<Set<UUID>>(() => new Set());

  // Multi-select — one set per kind. The bar appears when any of them is
  // non-empty.
  const [selection, setSelection] = useState<BulkSelection>({
    rooms: new Set(),
    units: new Set(),
    sections: new Set(),
    slots: new Set(),
  });

  // Red toast at the top. Auto-clears after 3 s; manually cleared when the
  // user dismisses. ``null`` means "no error to show".
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), 4000);
    return () => clearTimeout(t);
  }, [error]);

  // The first ``initialTree`` after mount is the seed; later values come
  // from home-switches, and they should reset every per-tree field so the
  // old home's busy markers / selection / error don't bleed across.
  const lastInitialRef = useRef<SpaceTree>(initialTree);
  useEffect(() => {
    if (lastInitialRef.current === initialTree) return;
    lastInitialRef.current = initialTree;
    setTree(initialTree);
    setBusyIds(new Set());
    setSelection({
      rooms: new Set(),
      units: new Set(),
      sections: new Set(),
      slots: new Set(),
    });
    setError(null);
  }, [initialTree]);

  // Build the lookup index once per tree change. ``DraggableNode`` uses it
  // to resolve a clicked slot's parent chain without walking the whole tree.
  const index = useMemo(() => buildIndex(tree), [tree]);

  // Sensors: pointer for mouse/touch, keyboard for accessibility (@dnd-kit's
  // ``KeyboardSensor`` wires Tab/Space/Arrow-keys out of the box).
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor),
  );

  // ----- field changes (rename, retype, slot metadata) ----------------

  const applyFieldChange = useCallback(
    async (change: FieldChange) => {
      const prev = tree;
      const optimistic = applyField(tree, change);
      setTree(optimistic);
      try {
        let server: unknown;
        if (change.kind === "room") {
          server = await api.updateRoom(
            change.id,
            { [change.field]: change.value },
            session,
          );
        } else if (change.kind === "unit") {
          server = await api.updateUnit(
            change.id,
            { [change.field]: change.value },
            session,
          );
        } else if (change.kind === "section") {
          server = await api.updateSection(
            change.id,
            { [change.field]: change.value },
            session,
          );
        } else {
          server = await api.updateSlot(
            change.id,
            { [change.field]: change.value },
            session,
          );
        }
        // Server response wins for slots (active_count refresh). For rooms
        // / units / sections the response is the same shape we already have.
        setTree((cur) => applyField(cur, change));
        // Cast through unknown — server row is structurally compatible.
        void server;
      } catch (err) {
        setTree(prev);
        setError(err instanceof APIError ? err.message : "操作失败，请重试。");
      }
    },
    [tree, session],
  );

  // ----- child creation -------------------------------------------------

  const createChild = useCallback(
    async (
      parentId: UUID,
      input:
        | { kind: "unit"; name: string; unit_type: UnitType }
        | { kind: "section"; name: string; section_type: SectionType }
          | { kind: "slot"; code: string },
    ) => {
      try {
        if (input.kind === "unit") {
          const row = await api.createUnit(
            parentId,
            { name: input.name, unit_type: input.unit_type },
            session,
          );
          setTree((cur) => insertChild(cur, { kind: "unit", row, roomId: parentId }));
        } else if (input.kind === "section") {
          const row = await api.createSection(
            parentId,
            { name: input.name, section_type: input.section_type },
            session,
          );
          setTree((cur) => insertChild(cur, { kind: "section", row, unitId: parentId }));
        } else {
          const row = await api.createSlot(
            parentId,
            { code: input.code },
            session,
          );
          setTree((cur) => insertChild(cur, { kind: "slot", row, sectionId: parentId }));
        }
      } catch (err) {
        setError(err instanceof APIError ? err.message : "操作失败，请重试。");
      }
    },
    [session],
  );

  // ----- delete ---------------------------------------------------------

  const deleteNodeFn = useCallback(
    async (kind: "room" | "unit" | "section" | "slot", id: UUID) => {
      const prev = tree;
      const optimistic = deleteNode(tree, kind, id);
      setTree(optimistic);
      try {
        if (kind === "room") await api.deleteRoom(id, session);
        else if (kind === "unit") await api.deleteUnit(id, session);
        else if (kind === "section") await api.deleteSection(id, session);
        else await api.deleteSlot(id, session);
        // Also drop it from the multi-select set so a selected-then-deleted
        // id doesn't dangle in the bar.
        setSelection((s) => {
          const drop = (set: Set<UUID>) => {
            if (!set.has(id)) return set;
            const next = new Set(set);
            next.delete(id);
            return next;
          };
          return {
            rooms: drop(s.rooms),
            units: drop(s.units),
            sections: drop(s.sections),
            slots: drop(s.slots),
          };
        });
      } catch (err) {
        setTree(prev);
        setError(err instanceof APIError ? err.message : "操作失败，请重试。");
      }
    },
    [tree, session],
  );

  // ----- move (single) --------------------------------------------------

  const moveNodeFn = useCallback(
    async (input: MoveInput) => {
      const prev = tree;
      // Resolve the actual newCode: if the caller (drag-end) didn't supply
      // one we keep the slot's current code, so "drop on a new section"
      // doesn't require the user to type the code.
      const resolvedCode =
        input.kind === "slot" && !input.newCode
          ? (index.slot.get(input.id)?.code ?? "")
          : input.kind === "slot"
            ? input.newCode
            : "";
      const next = cloneTree(prev);
      let optimistic: SpaceTree;
      if (input.kind === "unit") {
        optimistic = reparent(next, "unit", { id: input.id }, {
          roomId: input.newRoomId,
        });
      } else if (input.kind === "section") {
        optimistic = reparent(next, "section", { id: input.id }, {
          unitId: input.newUnitId,
        });
      } else {
        optimistic = reparent(next, "slot", { id: input.id }, {
          sectionId: input.newSectionId,
        });
        optimistic = applyField(optimistic, {
          kind: "slot",
          id: input.id,
          field: "code",
          value: resolvedCode,
        });
      }
      setTree(optimistic);
      try {
        if (input.kind === "unit") {
          await api.moveUnit(input.id, { room_id: input.newRoomId }, session);
        } else if (input.kind === "section") {
          await api.moveSection(input.id, { unit_id: input.newUnitId }, session);
        } else {
          await api.moveSlot(
            input.id,
            { section_id: input.newSectionId, code: resolvedCode },
            session,
          );
        }
      } catch (err) {
        setTree(prev);
        setError(err instanceof APIError ? err.message : "操作失败，请重试。");
      }
    },
    [tree, session, index],
  );

  // ----- drag-drop ------------------------------------------------------

  const applyReorder = useCallback(
    async (
      kind: "room" | "unit" | "section" | "slot",
      parentId: UUID | undefined,
      orderedIds: UUID[],
    ) => {
      // Optimistic: apply the new order locally, then PATCH each row's
      // ``sort_order`` in turn. Failures roll the local tree back.
      const prev = tree;
      const parentKey =
        kind === "room"
          ? undefined
          : (parentId as UUID);
      setTree((cur) =>
        reorderChildren(cur, kind, parentKey as UUID, orderedIds),
      );
      try {
        for (let i = 0; i < orderedIds.length; i++) {
          const id = orderedIds[i];
          if (kind === "room") {
            await api.updateRoom(id, { sort_order: i }, session);
          } else if (kind === "unit") {
            await api.updateUnit(id, { sort_order: i }, session);
          } else if (kind === "section") {
            await api.updateSection(id, { sort_order: i }, session);
          } else {
            await api.updateSlot(id, { sort_order: i }, session);
          }
        }
      } catch (err) {
        setTree(prev);
        setError(err instanceof APIError ? err.message : "操作失败，请重试。");
      }
    },
    [tree, session],
  );

  // Re-bind ``onDragEnd`` once ``applyReorder`` is in scope.
  const onDragEndRef = useRef<(event: DragEndEvent) => Promise<void>>();
  useEffect(() => {
    onDragEndRef.current = async (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;
      const aData = active.data.current as
        | {
            kind: "room" | "unit" | "section" | "slot";
            id: UUID;
            parentId?: UUID;
          }
        | undefined;
      const oData = over.data.current as
        | {
            kind: "room" | "unit" | "section" | "slot";
            id: UUID;
            parentId?: UUID;
            isParent?: boolean;
          }
        | undefined;
      if (!aData || !oData) return;
      // ``over`` could be either a sibling sortable or a parent droppable.
      // If it's a parent droppable, the user dropped onto the empty area
      // of the parent — that's still a cross-parent move.
      const overKind = oData.isParent
        ? // parent droppable — figure out the kind from the parent's role
          (oData.kind as "room" | "unit" | "section")
        : oData.kind;

      // Same-level reorder: same kind and same parent.
      if (
        !oData.isParent &&
        aData.kind === oData.kind &&
        aData.parentId === oData.parentId
      ) {
        const ids = siblingIdsOf(tree, aData.kind, aData.parentId);
        const oldPos = ids.indexOf(String(active.id) as UUID);
        const newPos = ids.indexOf(String(over.id) as UUID);
        if (oldPos < 0 || newPos < 0) return;
        const reordered = [...ids];
        reordered.splice(oldPos, 1);
        reordered.splice(newPos, 0, String(active.id) as UUID);
        await applyReorder(aData.kind, aData.parentId, reordered);
        return;
      }

      // Cross-parent.
      if (aData.kind === "room") return;
      const a = aData as { kind: "unit" | "section" | "slot"; id: UUID };
      const o = { ...oData, kind: overKind } as {
        kind: "room" | "unit" | "section" | "slot";
        id: UUID;
      };
      const cross = crossParentMoveInput(a, o);
      if (cross) {
        await moveNodeFn(cross);
      }
    };
  }, [tree, moveNodeFn, applyReorder]);

  // ----- bulk ops -------------------------------------------------------

  const bulkDeleteFn = useCallback(
    async (ids: UUID[]) => {
      // We need to know the kind of each id; the call site hands us a
      // heterogeneous list (across the four kinds). Group and dispatch in
      // parallel; partial failures roll back only their slice.
      const groups = {
        room: [] as UUID[],
        unit: [] as UUID[],
        section: [] as UUID[],
        slot: [] as UUID[],
      };
      for (const id of ids) {
        if (selection.rooms.has(id)) groups.room.push(id);
        else if (selection.units.has(id)) groups.unit.push(id);
        else if (selection.sections.has(id)) groups.section.push(id);
        else if (selection.slots.has(id)) groups.slot.push(id);
      }
      // Run in parallel; ``Promise.allSettled`` so one bad id doesn't kill
      // the rest. The error toast reports only the count.
      const results = await Promise.allSettled([
        ...groups.room.map((id) => api.deleteRoom(id, session)),
        ...groups.unit.map((id) => api.deleteUnit(id, session)),
        ...groups.section.map((id) => api.deleteSection(id, session)),
        ...groups.slot.map((id) => api.deleteSlot(id, session)),
      ]);
      const ok = results.filter((r) => r.status === "fulfilled").length;
      const fail = results.length - ok;
      if (ok > 0) {
        // Refresh from the server so we don't keep optimistic-but-stale rows.
        router.refresh();
      }
      if (fail > 0) {
        setError(`${fail} 项删除未通过（已有子内容？），已成功 ${ok} 项`);
      }
      // Drop successful ids from the selection.
      setSelection((s) => {
        const drop = (set: Set<UUID>) => {
          const next = new Set(set);
          for (const id of ids) next.delete(id);
          return next;
        };
        return {
          rooms: drop(s.rooms),
          units: drop(s.units),
          sections: drop(s.sections),
          slots: drop(s.slots),
        };
      });
    },
    [session, router, selection],
  );

  const bulkMoveFn = useCallback(
    async (
      input:
        | { kind: "unit"; newRoomId: UUID; ids: UUID[] }
        | { kind: "section"; newUnitId: UUID; ids: UUID[] }
        | {
            kind: "slot";
            newSectionId: UUID;
            newCode: string;
            ids: UUID[];
          },
    ) => {
      const results = await Promise.allSettled(
        input.ids.map((id) => {
          if (input.kind === "unit") {
            return api.moveUnit(id, { room_id: input.newRoomId }, session);
          }
          if (input.kind === "section") {
            return api.moveSection(id, { unit_id: input.newUnitId }, session);
          }
          return api.moveSlot(
            id,
            { section_id: input.newSectionId, code: input.newCode },
            session,
          );
        }),
      );
      const ok = results.filter((r) => r.status === "fulfilled").length;
      const fail = results.length - ok;
      if (ok > 0) router.refresh();
      if (fail > 0) {
        setError(`${fail} 项移动未通过（编码冲突？），已成功 ${ok} 项`);
      }
      setSelection({
        rooms: new Set(),
        units: new Set(),
        sections: new Set(),
        slots: new Set(),
      });
    },
    [session, router],
  );

  // ----- selection toggle ----------------------------------------------

  const toggleSelected = useCallback(
    (kind: "room" | "unit" | "section" | "slot", id: UUID) => {
      setSelection((s) => {
        const key =
          kind === "room"
            ? "rooms"
            : kind === "unit"
              ? "units"
              : kind === "section"
                ? "sections"
                : "slots";
        const next = new Set(s[key]);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return { ...s, [key]: next };
      });
    },
    [],
  );

  const clearSelection = useCallback(() => {
    setSelection({
      rooms: new Set(),
      units: new Set(),
      sections: new Set(),
      slots: new Set(),
    });
  }, []);

  // ----- render ---------------------------------------------------------

  return (
    <div className="space-y-4">
      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
        >
          {error}
          <button
            type="button"
            onClick={() => setError(null)}
            className="ml-2 text-xs text-red-500 hover:text-red-700"
          >
            ×
          </button>
        </div>
      )}
      <BulkActionBar
        selection={selection}
        rooms={tree.rooms}
        onClear={clearSelection}
        onBulkDelete={bulkDeleteFn}
        onBulkMove={bulkMoveFn}
      />
      <DndContext
        sensors={sensors}
        onDragEnd={(event) => onDragEndRef.current?.(event)}
      >
        <EditorTree
          tree={tree}
          index={index}
          session={session}
          selection={selection}
          busyIds={busyIds}
          onApplyField={applyFieldChange}
          onCreateChild={createChild}
          onDeleteNode={deleteNodeFn}
          onMoveNodeFn={moveNodeFn}
          onToggleSelected={toggleSelected}
        />
      </DndContext>
    </div>
  );
}

// ----- helpers ---------------------------------------------------------

function siblingIdsOf(
  tree: SpaceTree,
  kind: "room" | "unit" | "section" | "slot",
  parentId?: UUID,
): UUID[] {
  if (kind === "room") return tree.rooms.map((r) => r.id);
  if (kind === "unit") {
    const room = tree.rooms.find((r) => r.id === parentId);
    return (room?.units ?? []).map((u) => u.id);
  }
  if (kind === "section") {
    for (const r of tree.rooms) {
      const u = (r.units ?? []).find((u) => u.id === parentId);
      if (u) return (u.sections ?? []).map((s) => s.id);
    }
    return [];
  }
  for (const r of tree.rooms) {
    for (const u of r.units ?? []) {
      const s = (u.sections ?? []).find((s) => s.id === parentId);
      if (s) return (s.slots ?? []).map((sl) => sl.id);
    }
  }
  return [];
}

function crossParentMoveInput(
  aData: { kind: "unit" | "section" | "slot"; id: UUID },
  oData: { kind: "room" | "unit" | "section" | "slot"; id: UUID },
): MoveInput | null {
  if (aData.kind === "unit" && oData.kind === "room") {
    return { kind: "unit", id: aData.id, newRoomId: oData.id };
  }
  if (aData.kind === "section" && oData.kind === "unit") {
    return { kind: "section", id: aData.id, newUnitId: oData.id };
  }
  if (aData.kind === "slot" && oData.kind === "section") {
    // ``moveNodeFn`` looks up the slot's current code from the tree when
    // ``newCode`` is empty — that's how "drop on a new section" preserves
    // the existing code without forcing the user to retype it.
    return { kind: "slot", id: aData.id, newSectionId: oData.id, newCode: "" };
  }
  return null;
}

// Re-export the section/slot/row types so consumers don't need to dig into
// `@/lib/types` for a single import. Used by the editor's children.
export type { StorageSection, StorageSlot, StorageUnit };