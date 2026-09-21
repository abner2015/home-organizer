"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, APIError, type ApiSession } from "@/lib/api";
import { Spinner } from "@/components/States";
import type { CandidateView } from "@/lib/types";

export function RecommendationActions({
  recId,
  candidates,
  session,
}: {
  recId: string;
  candidates: CandidateView[];
  session: ApiSession;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function accept(c: CandidateView) {
    setBusy(`accept:${c.slot_id}`);
    setError(null);
    try {
      await api.acceptRecommendation(recId, {}, session);
      router.push("/items");
      router.refresh();
    } catch (err) {
      setError(extract(err, "保存失败"));
    } finally {
      setBusy(null);
    }
  }

  async function reject() {
    setBusy("reject");
    setError(null);
    try {
      await api.rejectRecommendation(recId, { note: "用户拒绝" }, session);
      router.refresh();
    } catch (err) {
      setError(extract(err, "操作失败"));
    } finally {
      setBusy(null);
    }
  }

  const recommended = candidates.find((c) => c.is_recommended) ?? candidates[0];
  return (
    <div className="mt-4 space-y-2">
      {error ? <p className="text-xs text-red-600">{error}</p> : null}
      {recommended ? (
        <div className="flex flex-wrap gap-2">
          <button
            className="btn-primary"
            onClick={() => accept(recommended)}
            disabled={busy !== null}
          >
            {busy === `accept:${recommended.slot_id}` ? <Spinner className="h-4 w-4" /> : null}
            确认放在这里
          </button>
          <button
            className="btn-secondary"
            onClick={reject}
            disabled={busy !== null}
          >
            不合适
          </button>
        </div>
      ) : null}
    </div>
  );
}

function extract(err: unknown, fallback: string): string {
  if (err instanceof APIError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}
