"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { APIError, type ApiSession, api } from "@/lib/api";
import { Spinner } from "@/components/States";

// "撤销排除" — the un-do of a rejection (P0.6). Shown on the recommendation
// detail page when status === "rejected"; clicking it calls
// POST /api/v1/recommendations/{id}/revoke which flips the row to "revoked"
// and brings the slot back into the candidate pool for the next recommend.
export function RevokeAction({
  recId,
  session,
}: {
  recId: string;
  session: ApiSession;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function revoke() {
    setBusy(true);
    setError(null);
    try {
      await api.revokeRecommendation(recId, session);
      router.refresh();
    } catch (err) {
      setError(extract(err, "撤销失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 space-y-2">
      {error ? <p className="text-xs text-red-600">{error}</p> : null}
      <button
        className="btn-secondary"
        onClick={revoke}
        disabled={busy}
        type="button"
      >
        {busy ? <Spinner className="h-4 w-4" /> : null}
        撤销排除
      </button>
      <p className="text-xs text-ink-500">
        撤销后，下一次推荐会把这个位置重新纳入候选。
      </p>
    </div>
  );
}

function extract(err: unknown, fallback: string): string {
  if (err instanceof APIError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}
