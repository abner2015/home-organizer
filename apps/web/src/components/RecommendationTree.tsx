"use client";

import { useState } from "react";
import { clsx } from "@/lib/format";
import type { CandidateView, StorageUnit, StorageSection, StorageSlot } from "@/lib/types";
import { formatConfidence } from "@/lib/format";

// ------------------------------------------------------------- tree builder

interface CabinetNode {
  unit: StorageUnit;
  recommendedSectionId: string | null;
  recommendedSlotId: string | null;
}

interface SectionNode {
  section: StorageSection;
  recommendedSlotId: string | null;
}

interface SlotNode {
  slot: StorageSlot;
  isRecommended: boolean;
  reason?: string;
  confidence?: number;
}

export function buildTreeFromCandidates(
  units: StorageUnit[],
  candidates: CandidateView[],
): {
  cabinets: CabinetNode[];
  recommended: CandidateView | null;
  alternatives: CandidateView[];
} {
  const recommended = candidates.find((c) => c.is_recommended) ?? candidates[0] ?? null;
  const recSlotId = recommended?.slot_id ?? null;
  const recSectionId = recommended ? findSectionIdForSlot(units, recSlotId) : null;
  const recUnitId = recommended ? findUnitIdForSlot(units, recSlotId) : null;

  const cabinets: CabinetNode[] = units.map((u) => ({
    unit: u,
    recommendedSectionId: u.id === recUnitId ? recSectionId : null,
    recommendedSlotId: u.id === recUnitId ? recSlotId : null,
  }));

  return {
    cabinets,
    recommended,
    alternatives: candidates.filter((c) => c.slot_id !== recSlotId),
  };
}

function findUnitIdForSlot(units: StorageUnit[], slotId: string | null): string | null {
  if (!slotId) return null;
  for (const u of units) {
    for (const s of u.sections ?? []) {
      if ((s.slots ?? []).some((sl) => sl.id === slotId)) return u.id;
    }
  }
  return null;
}
function findSectionIdForSlot(units: StorageUnit[], slotId: string | null): string | null {
  if (!slotId) return null;
  for (const u of units) {
    for (const s of u.sections ?? []) {
      if ((s.slots ?? []).some((sl) => sl.id === slotId)) return s.id;
    }
  }
  return null;
}

// ------------------------------------------------------------- component

export function RecommendationTree({
  units,
  candidates,
}: {
  units: StorageUnit[];
  candidates: CandidateView[];
}) {
  const { cabinets, recommended } = buildTreeFromCandidates(units, candidates);

  if (cabinets.length === 0) {
    return (
      <p className="text-sm text-ink-500">
        还没有任何收纳空间。
        <a className="text-brand-600 underline ml-1" href="/home/storage">
          立即添加
        </a>
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {cabinets.map(({ unit, recommendedSectionId, recommendedSlotId }) => {
        const isCabinetRecommended = recommendedSlotId !== null;
        return (
          <Cabinet
            key={unit.id}
            unit={unit}
            isRecommendedCabinet={isCabinetRecommended}
            recommendedSectionId={recommendedSectionId}
            recommendedSlotId={recommendedSlotId}
            candidates={candidates}
          />
        );
      })}
      {recommended ? (
        <div className="card mt-4 border-brand-200 bg-brand-50/50 p-4">
          <div className="flex items-start gap-2">
            <span className="badge-brand">⭐ 推荐</span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-ink-900">
                {recommended.room_name} · {recommended.unit_name} · {recommended.section_name} · {recommended.code}
              </p>
              <p className="mt-1 text-sm text-ink-700">{recommended.reason}</p>
              <p className="mt-2 text-xs text-ink-500">
                置信度 {formatConfidence(recommended.confidence)}
              </p>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Cabinet({
  unit,
  isRecommendedCabinet,
  recommendedSectionId,
  recommendedSlotId,
  candidates,
}: {
  unit: StorageUnit;
  isRecommendedCabinet: boolean;
  recommendedSectionId: string | null;
  recommendedSlotId: string | null;
  candidates: CandidateView[];
}) {
  const sections = unit.sections ?? [];
  return (
    <div
      className={clsx(
        "rounded-2xl border p-4 transition-colors",
        isRecommendedCabinet
          ? "border-brand-300 bg-brand-50/40"
          : "border-ink-100 bg-white",
      )}
    >
      <div className="flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-ink-100 text-ink-700 text-sm font-semibold">
          {unit.name.slice(0, 1)}
        </span>
        <h3 className="text-sm font-semibold text-ink-900">{unit.name}</h3>
        {isRecommendedCabinet ? <span className="badge-brand">推荐柜子</span> : null}
      </div>
      <div className="mt-3 space-y-3 pl-2">
        {sections.map((section) => {
          const isRecommendedSection = section.id === recommendedSectionId;
          return (
            <Section
              key={section.id}
              section={section}
              isRecommendedSection={isRecommendedSection}
              recommendedSlotId={recommendedSlotId}
              candidates={candidates}
            />
          );
        })}
      </div>
    </div>
  );
}

function Section({
  section,
  isRecommendedSection,
  recommendedSlotId,
  candidates,
}: {
  section: StorageSection;
  isRecommendedSection: boolean;
  recommendedSlotId: string | null;
  candidates: CandidateView[];
}) {
  const slots = section.slots ?? [];
  return (
    <div
      className={clsx(
        "rounded-xl border p-3",
        isRecommendedSection
          ? "border-brand-200 bg-white"
          : "border-ink-100 bg-ink-50/40",
      )}
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="text-ink-400">└─</span>
        <span className="text-sm font-medium text-ink-800">{section.name}</span>
        {isRecommendedSection ? <span className="badge-brand">推荐区域</span> : null}
      </div>
      <div className="space-y-1.5 pl-5">
        {slots.map((slot) => {
          const isRecommended = slot.id === recommendedSlotId;
          const cand = candidates.find((c) => c.slot_id === slot.id);
          return (
            <Slot
              key={slot.id}
              slot={slot}
              isRecommended={isRecommended}
              candidate={cand}
            />
          );
        })}
      </div>
    </div>
  );
}

function Slot({
  slot,
  isRecommended,
  candidate,
}: {
  slot: StorageSlot;
  isRecommended: boolean;
  candidate?: CandidateView;
}) {
  const [open, setOpen] = useState(isRecommended);
  return (
    <div
      className={clsx(
        "rounded-lg border px-3 py-2 transition-colors",
        isRecommended
          ? "border-brand-300 bg-brand-50"
          : "border-ink-100 bg-white hover:border-ink-200",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between text-left"
      >
        <div className="flex items-center gap-2">
          <span className="text-xs text-ink-400">└─</span>
          <span className="text-sm text-ink-800">
            <span className="font-mono text-xs text-ink-500">{slot.code}</span>
            {slot.label ? <span className="ml-2 text-ink-700">{slot.label}</span> : null}
          </span>
          {isRecommended ? <span className="badge-brand">⭐ 推荐</span> : null}
        </div>
        <span className="text-ink-400 text-xs">{open ? "收起" : "展开"}</span>
      </button>
      {open ? (
        <div className="mt-2 pl-5 text-xs text-ink-500">
          {candidate?.reason ? <p className="text-ink-700">{candidate.reason}</p> : null}
          {candidate?.confidence !== undefined ? (
            <p className="mt-1">置信度 {formatConfidence(candidate.confidence)}</p>
          ) : null}
          {slot.capacity_hint ? <p className="mt-1">容量：{slot.capacity_hint}</p> : null}
          {slot.allowed_categories && slot.allowed_categories.length > 0 ? (
            <p className="mt-1">允许：{slot.allowed_categories.join("、")}</p>
          ) : null}
          {slot.active_count !== undefined ? (
            <p className="mt-1">已放置 {slot.active_count} 件</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
