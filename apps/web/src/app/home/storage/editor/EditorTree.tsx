"use client";

// Recursive tree renderer. Each level renders its siblings inside a
// ``<SortableContext>`` (for in-place reorders) and wraps each parent in a
// ``<DroppableContainer>`` (so a sibling dragged onto the parent container
// itself — not onto another sibling — triggers a cross-parent move).
//
// The recursive shape mirrors the four-level hierarchy:
//
//   Room  →  Unit  →  Section  →  Slot
//
// Each level renders its row via ``<DraggableNode>`` and recurses into its
// children with the same component. The outermost call is rooms; the
// innermost is slots which have no children of their own.

import { useDroppable } from "@dnd-kit/core";
import {
  SortableContext,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";

import type { ApiSession } from "@/lib/api";
import type {
  SpaceTree,
  StorageSection,
  StorageSlot,
  StorageUnit,
  UUID,
} from "@/lib/types";

import { DraggableNode } from "./DraggableNode";
import { CreateChildForm, type CreateChildInput } from "./CreateChildForm";
import type { BulkSelection } from "./BulkActionBar";
import type { NodeIndex } from "./types";

export type EditorTreeProps = {
  tree: SpaceTree;
  index: NodeIndex;
  session: ApiSession;
  selection: BulkSelection;
  busyIds: Set<UUID>;
  onApplyField: (
    change:
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
        },
  ) => Promise<void>;
  onCreateChild: (
    parentId: UUID,
    input: CreateChildInput,
  ) => Promise<void>;
  onDeleteNode: (
    kind: "room" | "unit" | "section" | "slot",
    id: UUID,
  ) => Promise<void>;
  onMoveNodeFn: (
    input:
      | { kind: "unit"; id: UUID; newRoomId: UUID }
      | { kind: "section"; id: UUID; newUnitId: UUID }
      | { kind: "slot"; id: UUID; newSectionId: UUID; newCode: string },
  ) => Promise<void>;
  onToggleSelected: (
    kind: "room" | "unit" | "section" | "slot",
    id: UUID,
  ) => void;
};

export function EditorTree(props: EditorTreeProps) {
  return (
    <div className="space-y-4">
      <SortableContext
        items={props.tree.rooms.map((r) => String(r.id))}
        strategy={verticalListSortingStrategy}
      >
        {props.tree.rooms.map((room) => (
          <DroppableParent
            key={room.id}
            kind="room"
            id={room.id}
            empty={!room.units?.length}
          >
            <DraggableNode
              kind="room"
              id={room.id}
              parentId={undefined}
              name={room.name}
              type={room.room_type}
              detailHref={`/home/rooms/${room.id}`}
              busy={props.busyIds.has(room.id)}
              selection={props.selection}
              index={props.index}
              onRename={(n) =>
                props.onApplyField({
                  kind: "room",
                  id: room.id,
                  field: "name",
                  value: n,
                })
              }
              onChangeType={(t) =>
                props.onApplyField({
                  kind: "room",
                  id: room.id,
                  field: "room_type",
                  value: t,
                })
              }
              onDelete={() => props.onDeleteNode("room", room.id)}
              onCreateChild={(input) =>
                props.onCreateChild(room.id, input)
              }
              onToggleSelected={props.onToggleSelected}
              childCount={room.units?.length ?? 0}
            />
            {room.units && room.units.length > 0 && (
              <div className="mt-2 ml-4 space-y-2 border-l-2 border-ink-100 pl-3">
                <UnitList
                  room={room}
                  {...props}
                />
              </div>
            )}
          </DroppableParent>
        ))}
      </SortableContext>
    </div>
  );
}

function UnitList({
  room,
  ...props
}: EditorTreeProps & {
  room: SpaceTree["rooms"][number];
}) {
  return (
    <SortableContext
      items={room.units.map((u) => String(u.id))}
      strategy={verticalListSortingStrategy}
    >
      {room.units.map((unit) => (
        <DroppableParent
          key={unit.id}
          kind="unit"
          id={unit.id}
          empty={!unit.sections?.length}
        >
          <DraggableNode
            kind="unit"
            id={unit.id}
            parentId={room.id}
            name={unit.name}
            type={unit.unit_type}
            detailHref={`/home/storage/units/${unit.id}`}
            busy={props.busyIds.has(unit.id)}
            selection={props.selection}
            index={props.index}
            onRename={(n) =>
              props.onApplyField({
                kind: "unit",
                id: unit.id,
                field: "name",
                value: n,
              })
            }
            onChangeType={(t) =>
              props.onApplyField({
                kind: "unit",
                id: unit.id,
                field: "unit_type",
                value: t,
              })
            }
            onDelete={() => props.onDeleteNode("unit", unit.id)}
            onCreateChild={(input) =>
              props.onCreateChild(unit.id, input)
            }
            onToggleSelected={props.onToggleSelected}
            childCount={unit.sections?.length ?? 0}
          />
          {unit.sections && unit.sections.length > 0 && (
            <div className="mt-2 ml-4 space-y-2 border-l-2 border-ink-100 pl-3">
              <SectionList
                unit={unit}
                {...props}
              />
            </div>
          )}
        </DroppableParent>
      ))}
    </SortableContext>
  );
}

function SectionList({
  unit,
  ...props
}: EditorTreeProps & {
  unit: StorageUnit;
}) {
  return (
    <SortableContext
      items={unit.sections.map((s) => String(s.id))}
      strategy={verticalListSortingStrategy}
    >
      {unit.sections.map((section) => (
        <DroppableParent
          key={section.id}
          kind="section"
          id={section.id}
          empty={!section.slots?.length}
        >
          <DraggableNode
            kind="section"
            id={section.id}
            parentId={unit.id}
            name={section.name}
            type={section.section_type}
            detailHref={`/home/storage/sections/${section.id}`}
            busy={props.busyIds.has(section.id)}
            selection={props.selection}
            index={props.index}
            onRename={(n) =>
              props.onApplyField({
                kind: "section",
                id: section.id,
                field: "name",
                value: n,
              })
            }
            onChangeType={(t) =>
              props.onApplyField({
                kind: "section",
                id: section.id,
                field: "section_type",
                value: t,
              })
            }
            onDelete={() => props.onDeleteNode("section", section.id)}
            onCreateChild={(input) =>
              props.onCreateChild(section.id, input)
            }
            onToggleSelected={props.onToggleSelected}
            childCount={section.slots?.length ?? 0}
          />
          {section.slots && section.slots.length > 0 && (
            <div className="mt-2 ml-4 flex flex-wrap gap-2 border-l-2 border-ink-100 pl-3">
              <SlotList
                section={section}
                {...props}
              />
            </div>
          )}
        </DroppableParent>
      ))}
    </SortableContext>
  );
}

function SlotList({
  section,
  ...props
}: EditorTreeProps & {
  section: StorageSection;
}) {
  return (
    <SortableContext
      items={section.slots.map((s) => String(s.id))}
      strategy={verticalListSortingStrategy}
    >
      {section.slots.map((slot) => (
        <DraggableNode
          key={slot.id}
          kind="slot"
          id={slot.id}
          parentId={section.id}
          name={slot.label ?? slot.code}
          code={slot.code}
          type=""
          detailHref={`/home/storage/slots/${slot.id}`}
          busy={props.busyIds.has(slot.id)}
          selection={props.selection}
          index={props.index}
          onRename={() => Promise.resolve()}
          onChangeType={() => Promise.resolve()}
          onChangeCode={(c) =>
            props.onApplyField({
              kind: "slot",
              id: slot.id,
              field: "code",
              value: c,
            })
          }
          onDelete={() => props.onDeleteNode("slot", slot.id)}
          onCreateChild={() => Promise.resolve()}
          onToggleSelected={props.onToggleSelected}
        />
      ))}
    </SortableContext>
  );
}

// Droppable wrapper around a DraggableNode + its children. Doubles as the
// sort context's container so a drag from a sibling onto the *parent*
// (rather than onto a specific sibling) reads as a cross-parent move.
function DroppableParent({
  kind,
  id,
  empty,
  children,
}: {
  kind: "room" | "unit" | "section";
  id: UUID;
  // If the parent has no children, give the drop area a visible border so
  // users can still see where they'd drop.
  empty: boolean;
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({
    id: `parent:${kind}:${id}`,
    data: { kind, id, isParent: true },
  });
  return (
    <div
      ref={setNodeRef}
      className={`rounded-lg transition-colors ${
        isOver
          ? "ring-2 ring-brand-400 bg-brand-50/30"
          : empty
            ? "ring-1 ring-dashed ring-ink-200"
            : ""
      }`}
    >
      {children}
    </div>
  );
}

// Re-export so consumers can write ``import { CreateChildForm } from "./EditorTree"``
export { CreateChildForm };

// Helper used only by ``DraggableNode`` — keeps the import graph tight.
export type { StorageSlot };