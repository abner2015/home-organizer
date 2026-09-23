import Link from "next/link";
import { api } from "@/lib/api";
import { requireSession } from "@/lib/session.server";
import { PageHeader, StatCard } from "@/components/PageHeader";
import { EmptyState, ErrorState } from "@/components/States";
import type { Home, SpaceTree } from "@/lib/types";

export const dynamic = "force-dynamic";

interface HomeData {
  home: Home | null;
  tree: SpaceTree | null;
  currentUserId: string | null;
}

async function loadHome(): Promise<HomeData> {
  const session = await requireSession();
  try {
    const [home, tree, me] = await Promise.all([
      api.getHome(session.homeId, session).catch(() => null),
      api.getSpaceTree(session).catch(() => null),
      // Needed to gate the "管理成员" link to the actual owner of the home,
      // not just anyone who happens to have `owner_id` set. P0.8.
      api.me(session.token).catch(() => null),
    ]);
    return { home, tree, currentUserId: me?.id ?? null };
  } catch (err) {
    console.error("home load failed", err);
    return { home: null, tree: null, currentUserId: null };
  }
}

export default async function HomeOverview() {
  const data = await loadHome();
  const isOwner = !!(
    data.currentUserId && data.home?.owner_id === data.currentUserId
  );
  if (!data.tree) {
    return (
      <div>
        <PageHeader title="我的家" />
        <ErrorState title="无法连接到后端" description="请确认后端服务是否在运行。" />
      </div>
    );
  }
  const tree = data.tree;
  const unitCount = tree.rooms.reduce((acc, r) => acc + (r.units?.length ?? 0), 0);
  const sectionCount = tree.rooms.reduce(
    (acc, r) =>
      acc +
      (r.units ?? []).reduce((a, u) => a + (u.sections?.length ?? 0), 0),
    0,
  );
  const slotCount = tree.rooms.reduce(
    (acc, r) =>
      acc +
      (r.units ?? []).reduce(
        (a, u) => a + (u.sections ?? []).reduce((b, s) => b + (s.slots?.length ?? 0), 0),
        0,
      ),
    0,
  );

  if (tree.rooms.length === 0) {
    // A brand-new account lands here, and until this batch there was nothing it
    // could do: the storage tree was written only by the seed script, so every
    // recommendation answered "无符合硬规则的位置". The empty state offers the
    // action rather than reporting the absence.
    return (
      <div>
        <PageHeader title={tree.home?.name ?? "我的家"} description="家庭空间概览" />
        <EmptyState
          title="还没有收纳空间"
          description="描述一句、拍一张照片，或者自己一级一级搭起来 —— 有了柜子和格子，AI 才能告诉你东西该放哪儿。"
          action={
            <Link href="/home/setup" className="btn-primary">
              搭建我的家
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={tree.home?.name ?? "我的家"}
        description="家庭空间概览"
        actions={
          <>
            <Link href="/home/rooms" className="btn-secondary">
              管理房间
            </Link>
            <Link href="/home/storage" className="btn-primary">
              管理收纳空间
            </Link>
          </>
        }
      />
      {isOwner && tree.home?.id ? (
        // The members page is gated to owners server-side (the API returns
        // 403 for non-owner POST/PATCH/DELETE), but only owners see the link
        // here so the affordance isn't advertised to people who can't act on
        // it. `isOwner` compares the cookie's identity with the home's
        // `owner_id`; an `/auth/me` failure here just hides the link, not the
        // page itself.
        <div className="flex justify-end gap-2">
          {/* P0.9: anyone in the home can create a *new* home (the new home's
              OWNER is the caller regardless of their role here) — so the
              link is shown to all members, not just owners. */}
          <Link href="/home/new" className="btn-ghost text-sm">
            + 新家
          </Link>
          <Link
            href={`/home/${tree.home.id}/members`}
            className="btn-ghost text-sm"
          >
            管理成员 →
          </Link>
        </div>
      ) : (
        // Non-owners still need a way to add a home of their own.
        <div className="flex justify-end">
          <Link href="/home/new" className="btn-ghost text-sm">
            + 新家
          </Link>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="房间" value={tree.rooms.length} tone="brand" />
        <StatCard label="柜子/架子" value={unitCount} />
        <StatCard label="层/抽屉" value={sectionCount} />
        <StatCard label="格" value={slotCount} />
      </div>

      <section>
        <h2 className="mb-3 text-lg font-semibold text-ink-900">房间列表</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {tree.rooms.map((room) => (
            <Link
              key={room.id}
              href={`/home/storage?room=${room.id}`}
              className="card p-4 hover:border-brand-200 hover:shadow-soft transition-all"
            >
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="font-semibold text-ink-900">{room.name}</h3>
                  <p className="mt-0.5 text-xs text-ink-500">{room.room_type ?? "未分类"}</p>
                </div>
                <span className="badge">{room.units?.length ?? 0} 个柜子</span>
              </div>
              <div className="mt-3 flex flex-wrap gap-1">
                {(room.units ?? []).slice(0, 3).map((u) => (
                  <span key={u.id} className="badge">
                    {u.name}
                  </span>
                ))}
                {(room.units?.length ?? 0) > 3 ? (
                  <span className="badge">+{(room.units?.length ?? 0) - 3}</span>
                ) : null}
              </div>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
