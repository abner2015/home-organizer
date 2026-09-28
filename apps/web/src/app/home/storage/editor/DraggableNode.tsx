"use client";

// A single draggable / selectable / editable row. Used at all four depths —
// the kind + parent triple decides the affordances it shows.
//
// Rendered inside a ``<SortableContext>`` so it is part of the drag-drop
// reordering flow. Its parent (the room, unit, or section container) is a
// ``useDroppable`` so a child dragged onto the *container itself* (rather
// than a sibling) gets a cross-parent move instead of a reorder.

import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Link from "next/link";

import type { UUID } from "@/lib/types";
import type { BulkSelection } from "./BulkActionBar";
import { ConfirmDelete } from "./ConfirmDelete";
import { CreateChildForm, type CreateChildInput } from "./CreateChildForm";
import { EditableName } from "./EditableName";
import { EditableType } from "./EditableType";
import type { NodeIndex } from "./types";

export type DraggableNodeProps = {
  kind: "room" | "unit" | "section" | "slot";
  id: UUID;
  parentId?: UUID;
  name: string;
  // Slot rows show ``code`` instead of (or alongside) ``name``.
  code?: string;
  type: string;
  detailHref: string | null;
  busy: boolean;
  index: NodeIndex;
  selection: BulkSelection;
  // Field edits — ``kind`` is already known so callers don't have to repeat.
  onRename: (next: string) => Promise<void>;
  onChangeType: (next: string) => Promise<void>;
  onChangeCode?: (next: string) => Promise<void>;
  onDelete: () => Promise<void>;
  onCreateChild: (input: CreateChildInput) => Promise<void>;
  onToggleSelected: (kind: "room" | "unit" | "section" | "slot", id: UUID) => void;
  // Number of descendants — drives the draggable's accessible label.
  childCount?: number;
};

export function DraggableNode({
  kind,
  id,
  parentId,
  name,
  code,
  type,
  detailHref,
  busy,
  selection,
  onRename,
  onChangeType,
  onChangeCode,
  onDelete,
  onCreateChild,
  onToggleSelected,
  childCount,
}: DraggableNodeProps) {
  // Sortable: ``id`` doubles as the dnd id. ``data`` payload lets
  // ``onDragEnd`` identify kind/parent without re-resolving.
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({
      id: String(id),
      data: { kind, id, parentId },
    });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const isSelected = isIdSelected(selection, kind, id);

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`group flex items-center gap-2 rounded-lg border bg-white p-2 text-sm transition-colors ${
        isSelected ? "border-brand-400 bg-brand-50" : "border-ink-200"
      } ${busy ? "opacity-60" : ""}`}
    >
      {/* Drag handle — explicitly opt-in so clicks on the name don't drag. */}
      <button
        type="button"
        aria-label={`拖动 ${name}`}
        title="拖动以排序 / 移动到别的父节点"
        className="cursor-grab text-ink-400 hover:text-ink-600 active:cursor-grabbing"
        {...attributes}
        {...listeners}
      >
        ⋮⋮
      </button>

      {/* Multi-select checkbox — keeps a stable hit area even when the row
          is being edited inline. */}
      <input
        type="checkbox"
        checked={isSelected}
        onChange={() => onToggleSelected(kind, id)}
        aria-label={`选择 ${name}`}
        className="h-4 w-4 cursor-pointer"
      />

      {/* Name — click-to-edit; for slots the ``code`` is the editable name
          and ``label`` (not yet exposed in the inline form) is decorative. */}
      <div className="flex-1 min-w-0">
        {kind === "slot" ? (
          <EditableName
            value={code ?? ""}
            onCommit={onChangeCode ?? (async () => {})}
            disabled={busy}
            busy={busy}
            maxLength={50}
            as="code"
          />
        ) : (
          <EditableName
            value={name}
            onCommit={onRename}
            disabled={busy}
            busy={busy}
            maxLength={100}
          />
        )}
        {kind === "slot" && code && name && name !== code && (
          <span className="ml-2 text-xs text-ink-500">{name}</span>
        )}
      </div>

      {/* Type <select> — only the four kinds that have one (room / unit /
          section). Slots don't carry a type. */}
      {kind !== "slot" && (
        <EditableType
          kind={kind}
          value={type as never}
          onCommit={onChangeType}
          disabled={busy}
        />
      )}

      {/* Detail link — collapses the inline-controls sprawl. Only shown if
          a detail page actually exists for the kind (slots do, rooms do). */}
      {detailHref && (
        <Link
          href={detailHref}
          className="text-xs text-ink-400 hover:text-brand-600"
          title="查看详情"
        >
          ↗
        </Link>
      )}

      {/* Delete — two-step. */}
      <ConfirmDelete size="sm" onConfirm={onDelete} disabled={busy} />

      {/* Create-child form (only for non-leaf kinds). */}
      {kind !== "slot" && (
        <CreateChildForm
          kind={kind === "room" ? "unit" : kind === "unit" ? "section" : "slot"}
          busy={busy}
          onCreate={onCreateChild}
        />
      )}

      {childCount !== undefined && childCount > 0 && (
        <span className="text-[10px] text-ink-400" title="子节点数">
          {childCount}
        </span>
      )}
    </div>
  );
}

function isIdSelected(
  selection: BulkSelection,
  kind: "room" | "unit" | "section" | "slot",
  id: UUID,
): boolean {
  if (kind === "room") return selection.rooms.has(id);
  if (kind === "unit") return selection.units.has(id);
  if (kind === "section") return selection.sections.has(id);
  return selection.slots.has(id);
}