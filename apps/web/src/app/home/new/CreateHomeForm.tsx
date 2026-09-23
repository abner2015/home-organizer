"use client";

// 「新建一个家」表单 — P0.9.
//
// Calls `POST /api/v1/homes` (Bearer-only — there is no `X-Home-Id` to put in
// the header because the home does not exist yet). On success, swaps the
// stored `HOME_COOKIE` to the newly-created home so the next navigation lands
// inside it, then redirects to `/home/setup` — the same empty-state CTA the
// first-home flow already teaches the user.

import { useState } from "react";
import { useRouter } from "next/navigation";

import { HOME_COOKIE } from "@/lib/cookies";
import { Spinner } from "@/components/States";
import { APIError, type ApiSession, api } from "@/lib/api";
import { setSession, getSession } from "@/lib/session";
import type { Session } from "@/lib/session";

export function CreateHomeForm({
  session,
}: {
  // Just the parts `api.createHome` needs; the parent server component
  // hands us the full Session so we can also call `setSession` after success
  // without an extra round-trip to read cookies again.
  session: ApiSession & Pick<Session, "refreshToken" | "displayName">;
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    // Empty / whitespace is a client-side concern: the API would return 400
    // anyway, but rejecting locally gives a friendlier message and avoids a
    // round-trip on a value the user can fix in one keystroke.
    const trimmed = name.trim();
    if (!trimmed) {
      setBusy(false);
      setError("家名不能为空");
      return;
    }
    try {
      const home = await api.createHome({ name: trimmed }, session);
      // Swap the home cookie so the next navigation is already inside the
      // new home. `setSession` rewrites every cookie with the refresh
      // token's lifetime, which is what we want — same shape `persist()`
      // writes on signup.
      const current = getSession();
      if (current) {
        setSession({
          ...current,
          homeId: home.id,
        });
      } else {
        // No browser session yet (shouldn't happen — middleware already
        // bounced us if missing, and `requireSession` is the only caller
        // of this component) — fall back to a direct cookie write so the
        // router push lands inside the right home.
        document.cookie = `${HOME_COOKIE}=${encodeURIComponent(home.id)}; Path=/; SameSite=Lax`;
      }
      router.push("/home/setup");
    } catch (err) {
      setError(extract(err, "创建失败，请稍后重试"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="card mx-auto max-w-xl space-y-4 p-6"
      aria-label="新建一个家"
    >
      <label className="block">
        <span className="text-sm font-semibold text-ink-700">家名</span>
        <input
          type="text"
          className="mt-1 w-full rounded border border-ink-200 bg-white px-3 py-2 text-base text-ink-900 focus:border-brand-500 focus:outline-none"
          placeholder="例如：老家、新家、工作室"
          maxLength={100}
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
          disabled={busy}
        />
        <p className="mt-1 text-xs text-ink-500">
          名字可以是任何称呼 —— 老家、租的新家、工作室，都各自独立。
        </p>
      </label>

      {error ? (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      ) : null}

      <div className="flex items-center gap-3">
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? <Spinner className="h-4 w-4" /> : null}
          创建
        </button>
        <button
          type="button"
          className="btn-ghost"
          onClick={() => router.back()}
          disabled={busy}
        >
          取消
        </button>
      </div>
    </form>
  );
}

function extract(err: unknown, fallback: string): string {
  if (err instanceof APIError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}
