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

// The proposal as the user sees it on the confirm step. The fields below are
// **preview only** under P1.3 — accepting calls the server-side `acceptProposal`
// with the original persisted proposal, so edits here would not reach the DB.
// Users who want to change the structure re-propose from step 1.
type DraftSlot = {
  key: number;
  code: string;
  label: string;
};
type DraftSection = {
  key: number;
  name: string;
  section_type: SectionType;
  slots: DraftSlot[];
};
type DraftUnit = {
  key: number;
  name: string;
  unit_type: UnitType;
  sections: DraftSection[];
};
type DraftRoom = {
  key: number;
  name: string;
  room_type: RoomType;
  units: DraftUnit[];
};

let nextKey = 1;
const key = () => nextKey++;

function toDraft(proposal: StructureProposal): DraftRoom[] {
  return proposal.rooms.map((room) => ({
    key: key(),
    name: room.name,
    room_type: room.room_type,
    units: room.units.map((unit) => ({
      key: key(),
      name: unit.name,
      unit_type: unit.unit_type,
      sections: unit.sections.map((section) => ({
        key: key(),
        name: section.name,
        section_type: section.section_type,
        slots: section.slots.map((slot) => ({
          key: key(),
          code: slot.code,
          label: slot.label ?? "",
        })),
      })),
    })),
  }));
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
  const [proposalId, setProposalId] = useState<string | null>(null);

  const [failure, setFailure] = useState<string | null>(null);
  const [rejectNote, setRejectNote] = useState("");

  async function propose(body: { asset_id?: string; description?: string }) {
    setBusy(true);
    setError(null);
    try {
      const res = await api.proposeStructure(body, session);
      setSource(res.source);
      setRationale(res.proposal.rationale);
      setWarnings(res.warnings);
      setDraft(toDraft(res.proposal));
      setProposalId(res.proposal_id);
      setFailure(null);
      setRejectNote("");
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
      await propose({
        asset_id: uploaded.asset_id,
        description: description.trim() || undefined,
      });
    } catch (err) {
      setError(err instanceof APIError ? err.message : "上传照片失败，请重试。");
      setBusy(false);
    }
  }

  /**
   * Accept the persisted proposal in one round-trip.
   *
   * P1.3: the previous per-node POST loop is gone. The server walks the
   * stored JSONB tree and creates room → unit → section → slot under a
   * single transaction. A 409 (duplicate slot code, etc.) leaves the home
   * untouched, so the user can re-propose with a different description.
   */
  async function accept() {
    if (!proposalId) return;
    setBusy(true);
    setFailure(null);
    try {
      await api.acceptProposal(proposalId, {}, session);
      router.refresh();
      router.push("/home/storage");
    } catch (err) {
      setFailure(
        err instanceof APIError
          ? `${err.message}（提议不会创建任何结构，可在 /home/proposals 中查看或拒绝）`
          : "接受提议失败，请稍后再试。",
      );
      setBusy(false);
    }
  }

  async function reject() {
    if (!proposalId) return;
    setBusy(true);
    setFailure(null);
    try {
      await api.rejectProposal(proposalId, { note: rejectNote.trim() || null }, session);
      router.refresh();
      router.push("/home/proposals");
    } catch (err) {
      setFailure(
        err instanceof APIError ? err.message : "拒绝提议失败，请稍后再试。",
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
          <span className="text-sm text-ink-600">
            这是提议的预览。接受后整个结构会一次性建好；想改节点请返回上一步重新描述。
          </span>
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
          <RoomPreview key={room.key} room={room} />
        ))}
      </div>

      {failure ? <p className="text-sm text-red-600">{failure}</p> : null}

      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => void accept()}
          >
            {busy ? <Spinner className="h-4 w-4" /> : null}
            接受并创建
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={busy}
            onClick={() => setStep("input")}
          >
            返回重新描述
          </button>
          <a
            href="/home/proposals"
            className="text-sm text-brand-600 hover:text-brand-700"
          >
            去提议列表
          </a>
        </div>

        <details className="rounded-xl border border-ink-100 bg-white p-3 text-sm">
          <summary className="cursor-pointer text-ink-700">不需要？（写下理由后拒绝）</summary>
          <div className="mt-2 space-y-2">
            <input
              className="input w-full"
              placeholder="可选：为什么这个提议不合适"
              value={rejectNote}
              onChange={(e) => setRejectNote(e.target.value)}
              maxLength={512}
            />
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => void reject()}
            >
              {busy ? <Spinner className="h-4 w-4" /> : null}
              拒绝此提议
            </button>
          </div>
        </details>
      </div>
    </div>
  );
}

// ----------------------------------------------------------- read-only preview
//
// P1.3: accept applies the *server-side* stored proposal in one transaction,
// so anything the user typed into this draft would not reach the DB. We
// deliberately do not let them edit here — see the banner on the confirm
// step. The tree is still shown so a user can sanity-check what accepting
// will create.

function labelOf<T extends { value: string; label: string }>(
  options: readonly T[],
  value: string,
): string {
  return options.find((o) => o.value === value)?.label ?? value;
}

function RoomPreview({ room }: { room: DraftRoom }) {
  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-xs text-ink-500">房间</span>
        <span className="text-base font-semibold text-ink-900">{room.name}</span>
        <span className="text-xs text-ink-500">
          （{labelOf(ROOM_TYPE_OPTIONS, room.room_type)}）
        </span>
      </div>

      <div className="mt-3 space-y-2 pl-4">
        {room.units.map((unit) => (
          <UnitPreview key={unit.key} unit={unit} />
        ))}
      </div>
    </div>
  );
}

function UnitPreview({ unit }: { unit: DraftUnit }) {
  return (
    <div className="rounded-xl border border-ink-100 bg-ink-50/40 p-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-xs text-ink-500">柜子</span>
        <span className="text-sm font-medium text-ink-900">{unit.name}</span>
        <span className="text-xs text-ink-500">
          （{labelOf(UNIT_TYPE_OPTIONS, unit.unit_type)}）
        </span>
      </div>

      <div className="mt-2 space-y-2 pl-4">
        {unit.sections.map((section) => (
          <SectionPreview key={section.key} section={section} />
        ))}
      </div>
    </div>
  );
}

function SectionPreview({ section }: { section: DraftSection }) {
  return (
    <div className="rounded-lg bg-white p-2.5">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-xs text-ink-500">层</span>
        <span className="text-sm text-ink-900">{section.name}</span>
        <span className="text-xs text-ink-500">
          （{labelOf(SECTION_TYPE_OPTIONS, section.section_type)}）
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5 pl-6">
        {section.slots.map((slot) => (
          <span
            key={slot.key}
            className="inline-flex items-center gap-1 rounded-lg border border-ink-100 px-2 py-0.5"
          >
            <span className="font-mono text-xs text-ink-800">{slot.code}</span>
            {slot.label ? (
              <span className="text-xs text-ink-500">· {slot.label}</span>
            ) : null}
          </span>
        ))}
      </div>
    </div>
  );
}
