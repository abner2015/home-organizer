import Link from "next/link";
import { api } from "@/lib/api";
import { getSession } from "@/lib/session";
import { PageHeader, StatCard } from "@/components/PageHeader";
import { ErrorState } from "@/components/States";
import type { Home, SpaceTree } from "@/lib/types";

export const dynamic = "force-dynamic";

interface HomeData {
  home: Home | null;
  tree: SpaceTree | null;
}

async function loadHome(): Promise<HomeData> {
  const session = getSession();
  try {
    const [home, tree] = await Promise.all([
      api.getHome(session.homeId, session.userId, session.homeId).catch(() => null),
      api.getSpaceTree(session.userId, session.homeId).catch(() => null),
    ]);
    return { home, tree };
  } catch (err) {
    console.error("home load failed", err);
    return { home: null, tree: null };
  }
}

export default async function HomeOverview() {
  const data = await loadHome();
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
