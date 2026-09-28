"use client";

// Inline "add child" form, one per level. Each kind has its own copy
// because the field set and the API call differ:
//
// - unit: name + unit_type → POST /rooms/{roomId}/storage-units
// - section: name + section_type → POST /storage-units/{unitId}/sections
// - slot: code (enforced, 1-50) → POST /sections/{sectionId}/slots
//
// The form is collapsed by default. A small "+ 新增子节点" button toggles
// it open. The parent (``<DraggableNode>``) owns the API call.

import { useState } from "react";
import type {
  SectionType,
  UnitType,
} from "@/lib/types";

export type CreateChildFormProps = {
  kind: "unit" | "section" | "slot";
  busy?: boolean;
  onCreate: (input: CreateChildInput) => Promise<void>;
};

export type CreateChildInput =
  | { kind: "unit"; name: string; unit_type: UnitType }
  | { kind: "section"; name: string; section_type: SectionType }
  | { kind: "slot"; code: string };

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

export function CreateChildForm({
  kind,
  busy,
  onCreate,
}: CreateChildFormProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [unitType, setUnitType] = useState<UnitType>("cabinet");
  const [sectionType, setSectionType] = useState<SectionType>("layer");

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-[11px] text-brand-600 hover:text-brand-700 hover:underline"
      >
        + 新增{kind === "unit" ? "收纳家具" : kind === "section" ? "分区" : "收纳位"}
      </button>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (kind === "slot") {
      const trimmed = code.trim();
      if (!trimmed) return;
      await onCreate({ kind: "slot", code: trimmed });
      setCode("");
    } else {
      const trimmed = name.trim();
      if (!trimmed) return;
      if (kind === "unit") {
        await onCreate({ kind: "unit", name: trimmed, unit_type: unitType });
      } else {
        await onCreate({ kind: "section", name: trimmed, section_type: sectionType });
      }
      setName("");
    }
    setOpen(false);
  }

  return (
    <form
      onSubmit={submit}
      className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-ink-200 bg-white p-2 text-xs"
    >
      {kind === "slot" ? (
        <input
          autoFocus
          value={code}
          maxLength={50}
          onChange={(e) => setCode(e.target.value)}
          placeholder="编码 (A1, K1...)"
          className="input h-7 w-32 font-mono text-xs"
        />
      ) : (
        <>
          <input
            autoFocus
            value={name}
            maxLength={100}
            onChange={(e) => setName(e.target.value)}
            placeholder="名称"
            className="input h-7 text-xs"
          />
          <select
            value={kind === "unit" ? unitType : sectionType}
            onChange={(e) =>
              kind === "unit"
                ? setUnitType(e.target.value as UnitType)
                : setSectionType(e.target.value as SectionType)
            }
            className="input h-7 text-xs"
          >
            {(kind === "unit" ? UNIT_TYPES : SECTION_TYPES).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </>
      )}
      <button
        type="submit"
        disabled={busy}
        className="btn-primary h-7 px-2 text-xs"
      >
        新建
      </button>
      <button
        type="button"
        onClick={() => setOpen(false)}
        className="btn-ghost h-7 px-2 text-xs"
      >
        取消
      </button>
    </form>
  );
}