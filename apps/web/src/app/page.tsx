import Link from "next/link";
import { api } from "@/lib/api";
import { getSession } from "@/lib/session";
import { PageHeader, StatCard } from "@/components/PageHeader";
import { ItemGrid } from "@/components/Items";
import { ErrorState, Loading } from "@/components/States";
import { formatRelative } from "@/lib/format";
import type { Item, SpaceTree } from "@/lib/types";

export const dynamic = "force-dynamic";

interface DashboardData {
  items: Item[];
  total: number;
  tree: SpaceTree | null;
}

async function loadDashboard(): Promise<DashboardData> {
  const session = getSession();
  try {
    const [items, tree] = await Promise.all([
      api.listItems(session.userId, session.homeId, { page: 1, page_size: 6 }),
      api.getSpaceTree(session.userId, session.homeId).catch(() => null),
    ]);
    return { items: items.items, total: items.total, tree };
  } catch (err) {
    console.error("dashboard load failed", err);
    return { items: [], total: 0, tree: null };
  }
}

export default async function HomePage() {
  const data = await loadDashboard();
  const session = getSession();
  const itemCount = data.total;
  const roomCount = data.tree?.rooms?.length ?? 0;
  const slotCount = data.tree?.rooms?.reduce(
    (acc, r) =>
      acc +
      (r.units ?? []).reduce(
        (a, u) => a + (u.sections ?? []).reduce((b, s) => b + (s.slots ?? []).length, 0),
        0,
      ),
    0,
  ) ?? 0;

  if (!data.tree && itemCount === 0) {
    return (
      <div>
        <PageHeader
          title="欢迎使用 AI 家庭收纳管家"
          description="拍张照片，AI 自动识别并推荐收纳位置。"
          actions={
            <Link href="/items/new" className="btn-primary">
              + 添加第一件物品
            </Link>
          }
        />
        <ErrorState
          title="暂未连接到后端"
          description="无法读取你的家与物品数据。请确认后端服务已启动。"
        />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="我的家"
        description="一目了然地查看物品、空间和最近添加"
        actions={
          <>
            <Link href="/assistant" className="btn-secondary">
              ✨ AI 助手
            </Link>
            <Link href="/items/new" className="btn-primary">
              + 添加物品
            </Link>
          </>
        }
      />

      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="物品数量" value={itemCount} hint="已登记的物品" tone="brand" />
        <StatCard label="空间数量" value={slotCount} hint="具体格子" />
        <StatCard label="房间" value={roomCount} hint="个" />
        <StatCard
          label="未放置"
          value={data.items.filter((i) => !i.current_placement).length}
          hint="件"
        />
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        <Link
          href="/items/new"
          className="card flex items-center gap-3 p-4 hover:border-brand-200 hover:shadow-soft transition-all"
        >
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-brand-500 text-white">
            <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 5v14M5 12h14" strokeLinecap="round" />
            </svg>
          </span>
          <div>
            <p className="font-semibold text-ink-900">快速添加物品</p>
            <p className="text-xs text-ink-500">拍照 → AI 识别 → 推荐位置</p>
          </div>
        </Link>
        <Link
          href="/assistant"
          className="card flex items-center gap-3 p-4 hover:border-brand-200 hover:shadow-soft transition-all"
        >
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-amber-100 text-amber-600">
            <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 2 13.5 8.5 20 10l-6.5 1.5L12 18l-1.5-6.5L4 10l6.5-1.5L12 2Z" />
            </svg>
          </span>
          <div>
            <p className="font-semibold text-ink-900">问问 AI 助手</p>
            <p className="text-xs text-ink-500">「我的数据线在哪？」</p>
          </div>
        </Link>
        <Link
          href="/home/storage"
          className="card flex items-center gap-3 p-4 hover:border-brand-200 hover:shadow-soft transition-all"
        >
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-ink-100 text-ink-700">
            <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
              <rect x="3" y="4" width="18" height="16" rx="1.5" />
              <path d="M3 10h18M3 16h18" />
            </svg>
          </span>
          <div>
            <p className="font-semibold text-ink-900">管理收纳空间</p>
            <p className="text-xs text-ink-500">房间 / 柜子 / 层 / 格</p>
          </div>
        </Link>
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-ink-900">最近添加</h2>
          <Link href="/items" className="text-sm text-brand-600 hover:underline">
            查看全部 →
          </Link>
        </div>
        {data.items.length === 0 ? (
          <div className="card p-8 text-center text-sm text-ink-500">
            还没有任何物品。
            <Link href="/items/new" className="ml-1 text-brand-600 underline">
              添加第一件
            </Link>
          </div>
        ) : (
          <ItemGrid items={data.items} />
        )}
      </section>
    </div>
  );
}
