"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, APIError, type ApiSession } from "@/lib/api";
import { Spinner } from "@/components/States";
import { clsx } from "@/lib/format";
import type {
  ProposalSource,
  ProposalWarning,
  RoomType,
  SectionType,
  StructureProposal,
  UnitType,
} from "@/lib/types";
import { ROOM_TYPE_OPTIONS, SECTION_TYPE_OPTIONS, UNIT_TYPE_OPTIONS } from "./labels";

const SOURCE_LABEL: Record<ProposalSource, string> = {
  photo: "根据照片",
  text: "根据描述",
  template: "通用模板",
};

// `empty_vocabulary` is not a defect in the proposal — it is the truthful
// first-run answer (a new home has no category vocabulary, so no slot can be
// restricted to one). The others mean the server *edited* what the model said,
// which the user has to be told before confirming.
const WARNING_LABEL: Record<string, string> = {
  truncated: "内容过多，已按上限裁剪",
  duplicate_code: "同一层里有重复的编号，已去重",
  category_cleared: "部分物品类别不在你家现有词汇里，已清空该限制",
  possible_duplicate: "和你家已有的名称重名",
  empty_vocabulary: "你家还没有物品类别，所以格子暂时不限制类别",
};

// ------------------------------------------------------------------- draft

// The proposal as the user is editing it. Every node carries its own `keep`
// flag: an unticked node is never sent anywhere.
type DraftSlot = {
  key: number;
  keep: boolean;
  code: string;
  label: string;
  capacity_hint: string;
  allowed_categories: string[];
};
type DraftSection = {
  key: number;
  keep: boolean;
  name: string;
  section_type: SectionType;
  slots: DraftSlot[];
};
type DraftUnit = {
  key: number;
  keep: boolean;
  name: string;
  unit_type: UnitType;
  sections: DraftSection[];
};
type DraftRoom = {
  key: number;
  keep: boolean;
  name: string;
  room_type: RoomType;
  units: DraftUnit[];
};

let nextKey = 1;
const key = () => nextKey++;

function toDraft(proposal: StructureProposal): DraftRoom[] {
  return proposal.rooms.map((room) => ({
    key: key(),
    keep: true,
    name: room.name,
    room_type: room.room_type,
    units: room.units.map((unit) => ({
      key: key(),
      keep: true,
      name: unit.name,
      unit_type: unit.unit_type,
      sections: unit.sections.map((section) => ({
        key: key(),
        keep: true,
        name: section.name,
        section_type: section.section_type,
        slots: section.slots.map((slot) => ({
          key: key(),
          keep: true,
          code: slot.code,
          label: slot.label ?? "",
          capacity_hint: slot.capacity_hint ?? "",
          allowed_categories: slot.allowed_categories ?? [],
        })),
      })),
    })),
  }));
}

/** How many ticked nodes a run will create — the denominator of the progress. */
function countKept(rooms: DraftRoom[]): number {
  let total = 0;
  for (const room of rooms) {
    if (!room.keep) continue;
    total += 1;
    for (const unit of room.units) {
      if (!unit.keep) continue;
      total += 1;
      for (const section of unit.sections) {
        if (!section.keep) continue;
        total += 1;
        total += section.slots.filter((slot) => slot.keep).length;
      }
    }
  }
  return total;
}

// ------------------------------------------------------------------ component

type Step = "input" | "confirm";

export function ProposalFlow({ session }: { session: ApiSession }) {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement | null>(null);

  const [step, setStep] = useState<Step>("input");
  const [description, setDescription] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [source, setSource] = useState<ProposalSource>("text");
  const [rationale, setRationale] = useState("");
  const [warnings, setWarnings] = useState<ProposalWarning[]>([]);
  const [draft, setDraft] = useState<DraftRoom[]>([]);

  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function propose(body: { asset_id?: string; description?: string }) {
    setBusy(true);
    setError(null);
    try {
      const res = await api.proposeStructure(body, session);
      setSource(res.source);
      setRationale(res.proposal.rationale);
      setWarnings(res.warnings);
      setDraft(toDraft(res.proposal));
      setFailure(null);
      setStep("confirm");
    } catch (err) {
      setError(err instanceof APIError ? err.message : "生成提议失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function proposeFromFile() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      // The bytes go through the API (`/assets/upload`) rather than a presigned
      // PUT: this works with every storage backend, including the local one.
      const uploaded = await api.uploadAsset(file, session);
      await propose({ asset_id: uploaded.asset_id, description: description.trim() || undefined });
    } catch (err) {
      setError(err instanceof APIError ? err.message : "上传照片失败，请重试。");
      setBusy(false);
    }
  }

  /**
   * Create the ticked nodes, parents first.
   *
   * One POST per node, in order, rather than a batch endpoint: a failure names
   * the exact node, and whatever was already created stays — the user retries
   * the rest instead of losing a half-built home.
   */
  async function materialize() {
    const kept = countKept(draft);
    if (kept === 0) {
      setFailure("请至少勾选一个要创建的节点。");
      return;
    }
    setBusy(true);
    setFailure(null);
    setProgress({ done: 0, total: kept });
    let done = 0;
    try {
      for (const room of draft) {
        if (!room.keep) continue;
        const createdRoom = await api.createRoom(
          { name: room.name, room_type: room.room_type },
          session,
        );
        setProgress({ done: ++done, total: kept });
        for (const unit of room.units) {
          if (!unit.keep) continue;
          const createdUnit = await api.createUnit(
            createdRoom.id,
            { name: unit.name, unit_type: unit.unit_type },
            session,
          );
          setProgress({ done: ++done, total: kept });
          for (const section of unit.sections) {
            if (!section.keep) continue;
            const createdSection = await api.createSection(
              createdUnit.id,
              { name: section.name, section_type: section.section_type },
              session,
            );
            setProgress({ done: ++done, total: kept });
            for (const slot of section.slots) {
              if (!slot.keep) continue;
              await api.createSlot(
                createdSection.id,
                {
                  code: slot.code,
                  label: slot.label.trim() || null,
                  capacity_hint: slot.capacity_hint.trim() || null,
                  allowed_categories: slot.allowed_categories,
                },
                session,
              );
              setProgress({ done: ++done, total: kept });
            }
          }
        }
      }
      router.refresh();
      router.push("/home/storage");
    } catch (err) {
      setFailure(
        err instanceof APIError
          ? `${err.message}（已创建 ${done} 项，剩下的可以再点一次创建）`
          : "创建过程中出错，已建成的部分会保留。",
      );
      setBusy(false);
    }
  }

  if (step === "input") {
    return (
      <div className="card space-y-5 p-5">
        <div>
          <label className="label" htmlFor="setup-description">
            用一句话描述你家的收纳空间
          </label>
          <textarea
            id="setup-description"
            className="input min-h-24"
            placeholder="例如：我家厨房有个三层吊柜，客厅有一个电视柜"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={2000}
          />
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              className="btn-primary"
              disabled={busy || description.trim().length === 0}
              onClick={() => void propose({ description: description.trim() })}
            >
              {busy ? <Spinner className="h-4 w-4" /> : null}
              根据描述生成
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => void propose({})}
            >
              先用模板搭一个
            </button>
          </div>
          <p className="mt-1.5 text-xs text-ink-500">
            模板不调用 AI，立刻就能得到一套卧室 / 厨房 / 客厅的骨架。
          </p>
        </div>

        <div className="border-t border-ink-100 pt-4">
          <label className="label" htmlFor="setup-photo">
            或者拍一张照片
          </label>
          <input
            id="setup-photo"
            ref={fileRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            className="block w-full text-sm text-ink-600 file:mr-3 file:rounded-lg file:border-0 file:bg-brand-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-brand-700"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <div className="mt-2">
            <button
              type="button"
              className="btn-secondary"
              disabled={busy || !file}
              onClick={() => void proposeFromFile()}
            >
              {busy ? <Spinner className="h-4 w-4" /> : null}
              根据照片生成
            </button>
          </div>
          <p className="mt-1.5 text-xs text-ink-500">
            照片会和上面的描述一起发给模型。
          </p>
        </div>

        {error ? <p className="text-sm text-red-600">{error}</p> : null}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="card space-y-3 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="badge-brand">{SOURCE_LABEL[source]}</span>
          <span className="text-sm text-ink-600">勾选要创建的部分，可以改名或删掉</span>
        </div>
        {rationale ? <p className="text-sm text-ink-600">{rationale}</p> : null}
        {warnings.length > 0 ? (
          <div className="rounded-xl border border-amber-100 bg-amber-50/60 p-3">
            <ul className="list-disc space-y-1 pl-4 text-xs text-amber-800">
              {warnings.map((w, i) => (
                <li key={i}>
                  {WARNING_LABEL[w.kind] ?? w.kind}
                  {w.path ? <span className="text-amber-700">（{w.path}）</span> : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      <div className="space-y-3">
        {draft.map((room) => (
          <RoomCard
            key={room.key}
            room={room}
            onChange={(next) => setDraft(draft.map((r) => (r.key === next.key ? next : r)))}
            onRemove={() => setDraft(draft.filter((r) => r.key !== room.key))}
          />
        ))}
      </div>

      {failure ? <p className="text-sm text-red-600">{failure}</p> : null}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-primary"
          disabled={busy}
          onClick={() => void materialize()}
        >
          {busy ? <Spinner className="h-4 w-4" /> : null}
          确认搭建
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={busy}
          onClick={() => setStep("input")}
        >
          返回
        </button>
        {progress ? (
          <span className="text-sm text-ink-500">
            正在创建 {progress.done} / {progress.total}…
          </span>
        ) : null}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------- tree

function KeepBox({
  checked,
  onChange,
  children,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input
        type="checkbox"
        className="h-4 w-4 rounded border-ink-300"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {children}
    </label>
  );
}

function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      className="ml-auto text-xs text-ink-400 hover:text-red-600"
      onClick={onClick}
      aria-label={label}
      title={label}
    >
      × 删除
    </button>
  );
}

function RoomCard({
  room,
  onChange,
  onRemove,
}: {
  room: DraftRoom;
  onChange: (room: DraftRoom) => void;
  onRemove: () => void;
}) {
  function update(patch: Partial<DraftRoom>) {
    onChange({ ...room, ...patch });
  }
  return (
    <div className={clsx("card p-4", !room.keep && "opacity-50")}>
      <div className="flex flex-wrap items-center gap-2">
        <KeepBox checked={room.keep} onChange={(keep) => update({ keep })}>
          <span className="text-xs text-ink-500">房间</span>
        </KeepBox>
        <input
          className="input max-w-48"
          value={room.name}
          onChange={(e) => update({ name: e.target.value })}
          aria-label="房间名称"
        />
        <select
          className="input max-w-32"
          value={room.room_type}
          onChange={(e) => update({ room_type: e.target.value as RoomType })}
          aria-label="房间类型"
        >
          {ROOM_TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <RemoveButton label={`删除房间 ${room.name}`} onClick={onRemove} />
      </div>

      <div className="mt-3 space-y-2 pl-4">
        {room.units.map((unit) => (
          <UnitCard
            key={unit.key}
            unit={unit}
            onChange={(next) =>
              update({ units: room.units.map((u) => (u.key === next.key ? next : u)) })
            }
            onRemove={() => update({ units: room.units.filter((u) => u.key !== unit.key) })}
          />
        ))}
      </div>
    </div>
  );
}

function UnitCard({
  unit,
  onChange,
  onRemove,
}: {
  unit: DraftUnit;
  onChange: (unit: DraftUnit) => void;
  onRemove: () => void;
}) {
  function update(patch: Partial<DraftUnit>) {
    onChange({ ...unit, ...patch });
  }
  return (
    <div
      className={clsx(
        "rounded-xl border border-ink-100 bg-ink-50/40 p-3",
        !unit.keep && "opacity-50",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <KeepBox checked={unit.keep} onChange={(keep) => update({ keep })}>
          <span className="text-xs text-ink-500">柜子</span>
        </KeepBox>
        <input
          className="input max-w-44"
          value={unit.name}
          onChange={(e) => update({ name: e.target.value })}
          aria-label="柜子名称"
        />
        <select
          className="input max-w-32"
          value={unit.unit_type}
          onChange={(e) => update({ unit_type: e.target.value as UnitType })}
          aria-label="柜子类型"
        >
          {UNIT_TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <RemoveButton label={`删除柜子 ${unit.name}`} onClick={onRemove} />
      </div>

      <div className="mt-2 space-y-2 pl-4">
        {unit.sections.map((section) => (
          <SectionCard
            key={section.key}
            section={section}
            onChange={(next) =>
              update({ sections: unit.sections.map((s) => (s.key === next.key ? next : s)) })
            }
            onRemove={() =>
              update({ sections: unit.sections.filter((s) => s.key !== section.key) })
            }
          />
        ))}
      </div>
    </div>
  );
}

function SectionCard({
  section,
  onChange,
  onRemove,
}: {
  section: DraftSection;
  onChange: (section: DraftSection) => void;
  onRemove: () => void;
}) {
  function update(patch: Partial<DraftSection>) {
    onChange({ ...section, ...patch });
  }
  return (
    <div className={clsx("rounded-lg bg-white p-2.5", !section.keep && "opacity-50")}>
      <div className="flex flex-wrap items-center gap-2">
        <KeepBox checked={section.keep} onChange={(keep) => update({ keep })}>
          <span className="text-xs text-ink-500">层</span>
        </KeepBox>
        <input
          className="input max-w-40"
          value={section.name}
          onChange={(e) => update({ name: e.target.value })}
          aria-label="层名称"
        />
        <select
          className="input max-w-28"
          value={section.section_type}
          onChange={(e) => update({ section_type: e.target.value as SectionType })}
          aria-label="层类型"
        >
          {SECTION_TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <RemoveButton label={`删除层 ${section.name}`} onClick={onRemove} />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2 pl-6">
        {section.slots.map((slot) => (
          <span
            key={slot.key}
            className={clsx(
              "inline-flex items-center gap-1 rounded-lg border border-ink-100 px-2 py-1",
              !slot.keep && "opacity-50",
            )}
          >
            <input
              type="checkbox"
              className="h-3.5 w-3.5 rounded border-ink-300"
              checked={slot.keep}
              onChange={(e) =>
                update({
                  slots: section.slots.map((s) =>
                    s.key === slot.key ? { ...s, keep: e.target.checked } : s,
                  ),
                })
              }
              aria-label={`保留 ${slot.code}`}
            />
            <input
              className="w-16 rounded border-0 bg-transparent p-0 font-mono text-xs text-ink-800 focus:outline-none"
              value={slot.code}
              onChange={(e) =>
                update({
                  slots: section.slots.map((s) =>
                    s.key === slot.key ? { ...s, code: e.target.value } : s,
                  ),
                })
              }
              aria-label="格子编号"
            />
            <input
              className="w-20 rounded border-0 bg-transparent p-0 text-xs text-ink-600 focus:outline-none"
              placeholder="备注"
              value={slot.label}
              onChange={(e) =>
                update({
                  slots: section.slots.map((s) =>
                    s.key === slot.key ? { ...s, label: e.target.value } : s,
                  ),
                })
              }
              aria-label="格子备注"
            />
            <button
              type="button"
              className="text-xs text-ink-400 hover:text-red-600"
              onClick={() =>
                update({ slots: section.slots.filter((s) => s.key !== slot.key) })
              }
              aria-label={`删除 ${slot.code}`}
            >
              ×
            </button>
          </span>
        ))}
        <button
          type="button"
          className="text-xs text-brand-600 hover:text-brand-700"
          onClick={() =>
            update({
              slots: [
                ...section.slots,
                {
                  key: key(),
                  keep: true,
                  code: `S${section.slots.length + 1}`,
                  label: "",
                  capacity_hint: "",
                  allowed_categories: [],
                },
              ],
            })
          }
        >
          + 加一格
        </button>
      </div>
    </div>
  );
}
