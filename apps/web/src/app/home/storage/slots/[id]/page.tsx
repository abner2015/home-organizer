import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { requireSession } from "@/lib/session.server";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState } from "@/components/States";
import { formatDateTime } from "@/lib/format";
import type { SlotDetail as SlotDetailType } from "@/lib/types";

export const dynamic = "force-dynamic";

const CAPACITY_LABEL: Record<string, string> = {
  small: "小",
  medium: "中",
  large: "大",
};

async function loadSlot(id: string): Promise<{
  detail: SlotDetailType | null;
  breadcrumb: string | null;
  error: string | null;
}> {
  const session = await requireSession();
  try {
    const detail = await api.getSlot(id, session);
    // Build a friendly back-link: home → unit → section. Walk the cheap
    // getSpaceTree so we don't grow a "GET slot's parent chain" route.
    const tree = await api.getSpaceTree(session).catch(() => null);
    let breadcrumb: string | null = null;
    if (tree) {
      outer: for (const room of tree.rooms) {
        for (const u of room.units ?? []) {
          for (const sec of u.sections ?? []) {
            if ((sec.slots ?? []).some((s) => s.id === id)) {
              breadcrumb = `${room.name} / ${u.name} / ${sec.name}`;
              break outer;
            }
          }
        }
      }
    }
    return { detail, breadcrumb, error: null };
  } catch (err) {
    console.error("slot load failed", err);
    return { detail: null, breadcrumb: null, error: "无法加载格子详情" };
  }
}

export default async function SlotDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const data = await loadSlot(params.id);
  if (data.error && !data.detail) {
    return (
      <div>
        <PageHeader title="格子详情" />
        <ErrorState title={data.error} />
      </div>
    );
  }
  if (!data.detail) {
    notFound();
  }
  const { slot, current_items } = data.detail!;
  return (
    <div className="space-y-6">
      <PageHeader
        title={slot.label || slot.code}
        description={data.breadcrumb ?? undefined}
        actions={
          <Link href="/home/storage" className="btn-ghost">
            ← 返回 收纳空间
          </Link>
        }
      />

      <section className="card p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
          基本信息
        </h2>
        <dl className="mt-3 grid grid-cols-2 gap-4 text-sm">
          <div>
            <dt className="text-ink-500">代号</dt>
            <dd className="font-mono font-medium text-ink-900">{slot.code}</dd>
          </div>
          <div>
            <dt className="text-ink-500">容量</dt>
            <dd className="font-medium text-ink-900">
              {slot.capacity_hint
                ? CAPACITY_LABEL[slot.capacity_hint] ?? slot.capacity_hint
                : "—"}
            </dd>
          </div>
          <div className="col-span-2">
            <dt className="text-ink-500">允许的分类</dt>
            <dd className="mt-1 flex flex-wrap gap-1">
              {slot.allowed_categories && slot.allowed_categories.length > 0 ? (
                slot.allowed_categories.map((c) => (
                  <span key={c} className="badge">
                    {c}
                  </span>
                ))
              ) : (
                <span className="text-sm text-ink-500">不限</span>
              )}
            </dd>
          </div>
        </dl>
      </section>

      <section className="card p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
            当前格子里的物品
          </h2>
          <span className="badge-brand">{current_items.length} 件</span>
        </div>
        {current_items.length === 0 ? (
          <div className="mt-4">
            <EmptyState
              title="这一格目前是空的"
              description="前往物品详情页，把物品放到这个格子。"
            />
          </div>
        ) : (
          <ol className="mt-3 space-y-2">
            {current_items.map((row) => (
              <li
                key={row.item_id}
                className="flex items-center justify-between rounded-lg bg-ink-50 px-3 py-2"
              >
                <Link
                  href={`/items/${row.item_id}`}
                  className="text-sm font-medium text-ink-900 transition-colors hover:text-brand-600"
                >
                  {row.item_name}
                </Link>
                <span className="text-xs text-ink-500">
                  {formatDateTime(row.placed_at)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}