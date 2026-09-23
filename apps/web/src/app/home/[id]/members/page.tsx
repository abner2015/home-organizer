import Link from "next/link";

import { api } from "@/lib/api";
import { requireSession } from "@/lib/session.server";
import { PageHeader } from "@/components/PageHeader";
import { ErrorState } from "@/components/States";
import { MembersClient } from "@/components/members/MembersClient";
import type { Member } from "@/lib/types";

// Always re-render so an invite / role change / remove is reflected without a
// full page reload sitting around with stale data. The router.refresh() inside
// MembersClient covers in-session mutations; `force-dynamic` covers the case
// where the user navigates here from another tab.
export const dynamic = "force-dynamic";

/**
 * `/home/[id]/members` — list, invite, role-change, remove (P0.8).
 *
 * The `[id]` in the URL is checked against `session.homeId`: a stale link to
 * a home the caller no longer belongs to looks the same as a link to a home
 * that does not exist at all (the "404 not 403" project convention — leaking
 * "this home exists but you can't see it" is the thing we avoid).
 *
 * Membership itself is read on the server (so the initial render can show the
 * list without a client-side flash), but every mutation is in the client
 * child — a `router.refresh()` re-runs the loader after each one.
 */
export default async function MembersPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;
  // 404 for cross-home URL — same shape as the backend's `ensure_member`
  // (404 not 403). Renders the standard empty-state card so the user gets
  // feedback rather than a silent mismatch.
  if (id !== session.homeId) {
    return (
      <div>
        <PageHeader title="成员管理" />
        <ErrorState
          title="找不到这个家"
          description="可能链接已过期，或这个家已经不属于你了。"
        />
      </div>
    );
  }

  // Need the caller's user_id to gate the owner-only controls (invite form,
  // role dropdown, remove button). The session cookie only carries
  // `displayName`, not `userId`, so we hit `GET /auth/me` once during render.
  // Same call shape the items/members API uses; same JWT in the header.
  let currentUserId: string | null = null;
  let members: Member[] = [];
  let loadError: string | null = null;
  try {
    const [me, list] = await Promise.all([
      api.me(session.token),
      api.listHomeMembers(session),
    ]);
    currentUserId = me.id;
    members = list;
  } catch (err) {
    console.error("members load failed", err);
    loadError = "无法加载成员列表";
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="成员管理"
        description="查看并管理谁能一起看到这个家"
        actions={
          <Link href="/home" className="btn-secondary">
            返回
          </Link>
        }
      />
      {loadError ? (
        <ErrorState title={loadError} description="请确认后端 API 可用。" />
      ) : (
        <MembersClient
          initialMembers={members}
          session={session}
          currentUserId={currentUserId}
        />
      )}
    </div>
  );
}