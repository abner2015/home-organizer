"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { APIError, type ApiSession, api } from "@/lib/api";
import { Spinner } from "@/components/States";
import type { UUID } from "@/lib/types";

// 「编辑备注」 — PATCH /placements/{id} with body `{note}` only.
// Shown on the item detail page for the active placement; clicking "编辑备注"
// reveals an inline input that calls `api.updatePlacement(id, {note}, ...)`.
// Moving to a different slot is a separate flow (PlaceItemButton → POST).
export function EditPlacementNote({
  placementId,
  initialNote,
  session,
}: {
  placementId: UUID;
  initialNote: string | null;
  session: ApiSession;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(initialNote ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    // Treat empty input as "clear the note". `null` vs string matters server-
    // side (model_fields_set distinguishes them), so always send a key.
    const next = value.trim() === "" ? null : value;
    try {
      await api.updatePlacement(placementId, { note: next }, session);
      setEditing(false);
      router.refresh();
    } catch (err) {
      setError(extract(err, "保存备注失败"));
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    setValue(initialNote ?? "");
    setError(null);
    setEditing(false);
  }

  if (!editing) {
    return (
      <div className="mt-1">
        {initialNote ? (
          <p className="text-xs text-ink-500">备注：{initialNote}</p>
        ) : (
          <p className="text-xs text-ink-400">（无备注）</p>
        )}
        <button
          type="button"
          className="btn-ghost !px-2 !py-0.5 !text-xs"
          onClick={() => setEditing(true)}
        >
          {initialNote ? "编辑备注" : "添加备注"}
        </button>
      </div>
    );
  }

  return (
    <div className="mt-1 space-y-2">
      <textarea
        className="w-full rounded border border-ink-200 bg-white px-2 py-1 text-sm text-ink-800 focus:border-brand-500 focus:outline-none"
        rows={2}
        maxLength={500}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="例如：常用、放在上层方便拿"
      />
      {error ? <p className="text-xs text-red-600">{error}</p> : null}
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn-secondary !px-2.5 !py-1 !text-xs"
          onClick={() => void save()}
          disabled={busy}
        >
          {busy ? <Spinner className="h-3.5 w-3.5" /> : null}
          保存
        </button>
        <button
          type="button"
          className="btn-ghost !px-2.5 !py-1 !text-xs"
          onClick={cancel}
          disabled={busy}
        >
          取消
        </button>
      </div>
    </div>
  );
}

function extract(err: unknown, fallback: string): string {
  if (err instanceof APIError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}