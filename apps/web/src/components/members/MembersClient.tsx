"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { APIError, type ApiSession, api } from "@/lib/api";
import { Spinner } from "@/components/States";
import type { HomeRole, Member, UUID } from "@/lib/types";

/**
 * Members page client (P0.8). Three controls, all owner-only:
 *
 * - **邀请成员** — email input + role select + 邀请 button. The 404
 *   message "该邮箱还没注册账号" is surfaced inline so the caller knows to
 *   ask the friend to register first (no SMTP path in this build).
 * - **改角色** — per-row dropdown. Demoting the last owner returns 409
 *   and the row stays untouched; we surface "至少需要保留一个 owner".
 * - **移除** — per-row button. Removing the last owner also returns 409
 *   with the same message.
 *
 * Non-owners see the list read-only (no invite form, dropdowns disabled,
 * no remove button). The "view" is intentionally identical for everyone;
 * only the affordances differ — that way members can see who else shares
 * the home without being told "you can't manage this".
 */
export function MembersClient({
  initialMembers,
  session,
  currentUserId,
}: {
  initialMembers: Member[];
  session: ApiSession;
  currentUserId: string | null;
}) {
  const router = useRouter();
  // Derived once per render — re-derived after `router.refresh()` re-runs
  // the loader and re-mounts this component with fresh props.
  const isOwner = useMemo(
    () =>
      !!currentUserId &&
      initialMembers.some(
        (m) => m.user_id === currentUserId && m.role === "owner",
      ),
    [initialMembers, currentUserId],
  );
  // Toast for last-owner 409 — the only error the caller cannot recover
  // from by re-reading the form (the only sensible action is "find another
  // owner first" or "don't").
  const [toast, setToast] = useState<string | null>(null);

  return (
    <div className="space-y-6">
      {toast ? (
        <div
          role="alert"
          className="card border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
          onClick={() => setToast(null)}
        >
          {toast}
        </div>
      ) : null}

      {isOwner ? (
        <InviteForm
          session={session}
          onError={setToast}
          onInvited={() => router.refresh()}
        />
      ) : (
        <div className="card px-4 py-3 text-sm text-ink-500">
          只有 owner 可以邀请 / 调整成员。
        </div>
      )}

      <div className="card divide-y divide-ink-100">
        {initialMembers.length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-ink-500">还没有成员</p>
        ) : (
          initialMembers.map((member) => (
            <MemberRow
              key={member.user_id}
              member={member}
              session={session}
              isOwnerViewer={isOwner}
              isSelf={member.user_id === currentUserId}
              onMutated={() => router.refresh()}
              onError={setToast}
            />
          ))
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------- invite form

function InviteForm({
  session,
  onInvited,
  onError,
}: {
  session: ApiSession;
  onInvited: () => void;
  onError: (msg: string | null) => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<HomeRole>("member");
  const [busy, setBusy] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setInlineError(null);
    onError(null);
    try {
      await api.inviteHomeMember({ email: email.trim(), role }, session);
      setEmail("");
      setRole("member");
      onInvited();
    } catch (err) {
      // 404 not_found from the backend = "该邮箱还没注册账号". Inline so the
      // caller reads it next to the email field; everything else is a banner.
      if (err instanceof APIError && err.status === 404) {
        setInlineError("该邮箱还没注册账号 — 让 TA 先注册，再回来邀请");
      } else {
        onError(extract(err, "邀请失败"));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card space-y-3 p-4">
      <h2 className="text-base font-semibold text-ink-900">邀请成员</h2>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="flex-1 text-sm">
          <span className="mb-1 block text-xs text-ink-500">邮箱</span>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="friend@example.com"
            className="w-full rounded border border-ink-200 bg-white px-3 py-2 text-sm text-ink-800 focus:border-brand-500 focus:outline-none"
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-xs text-ink-500">角色</span>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as HomeRole)}
            className="rounded border border-ink-200 bg-white px-3 py-2 text-sm text-ink-800 focus:border-brand-500 focus:outline-none"
          >
            <option value="member">member</option>
            <option value="owner">owner</option>
          </select>
        </label>
        <button
          type="submit"
          className="btn-primary whitespace-nowrap"
          disabled={busy || email.trim() === ""}
        >
          {busy ? <Spinner className="h-4 w-4" /> : null}
          邀请
        </button>
      </div>
      {inlineError ? (
        <p className="text-sm text-red-600">{inlineError}</p>
      ) : null}
      <p className="text-xs text-ink-400">
        对方需要先注册账号；这里不做邮件邀请。
      </p>
    </form>
  );
}

// --------------------------------------------------------------- member row

function MemberRow({
  member,
  session,
  isOwnerViewer,
  isSelf,
  onMutated,
  onError,
}: {
  member: Member;
  session: ApiSession;
  isOwnerViewer: boolean;
  isSelf: boolean;
  onMutated: () => void;
  onError: (msg: string | null) => void;
}) {
  const [busy, setBusy] = useState<"role" | "remove" | null>(null);

  async function changeRole(next: HomeRole) {
    if (next === member.role) return;
    setBusy("role");
    onError(null);
    try {
      await api.updateHomeMember(
        member.user_id,
        { role: next },
        session,
      );
      onMutated();
    } catch (err) {
      if (err instanceof APIError && err.status === 409) {
        onError("至少需要保留一个 owner");
      } else {
        onError(extract(err, "修改角色失败"));
      }
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!confirm(`确定要把 ${member.display_name} 从这个家移除吗？`)) return;
    setBusy("remove");
    onError(null);
    try {
      await api.removeHomeMember(member.user_id, session);
      onMutated();
    } catch (err) {
      if (err instanceof APIError && err.status === 409) {
        onError("至少需要保留一个 owner");
      } else {
        onError(extract(err, "移除失败"));
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand-50 text-sm font-semibold text-brand-600">
          {member.display_name.slice(0, 1).toUpperCase()}
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-medium text-ink-900">
              {member.display_name}
              {isSelf ? (
                <span className="ml-2 text-xs text-ink-400">（我）</span>
              ) : null}
            </p>
            <RoleBadge role={member.role} />
          </div>
          <p className="truncate text-xs text-ink-500">{member.email}</p>
          <p className="text-xs text-ink-400">
            加入时间：{new Date(member.joined_at).toLocaleDateString("zh-CN")}
          </p>
        </div>
      </div>
      {isOwnerViewer ? (
        <div className="flex items-center gap-2">
          <select
            aria-label="角色"
            value={member.role}
            disabled={busy !== null}
            onChange={(e) => void changeRole(e.target.value as HomeRole)}
            className="rounded border border-ink-200 bg-white px-2 py-1 text-xs text-ink-800 focus:border-brand-500 focus:outline-none disabled:opacity-50"
          >
            <option value="member">member</option>
            <option value="owner">owner</option>
          </select>
          <button
            type="button"
            onClick={() => void remove()}
            disabled={busy !== null}
            className="btn-ghost !px-2.5 !py-1 !text-xs text-red-600 disabled:opacity-50"
          >
            {busy === "remove" ? <Spinner className="h-3.5 w-3.5" /> : "移除"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function RoleBadge({ role }: { role: HomeRole }) {
  return (
    <span
      className={
        role === "owner"
          ? "badge border-brand-200 bg-brand-50 text-brand-700"
          : "badge"
      }
    >
      {role}
    </span>
  );
}

function extract(err: unknown, fallback: string): string {
  if (err instanceof APIError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}