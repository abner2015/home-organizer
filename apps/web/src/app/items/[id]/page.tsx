import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { requireSession } from "@/lib/session.server";
import { PageHeader } from "@/components/PageHeader";
import { ErrorState } from "@/components/States";
import { PlaceItemButton } from "@/components/placements/PlaceItemButton";
import { RemovePlacementButton } from "@/components/placements/RemovePlacementButton";
import { EditPlacementNote } from "@/components/placements/EditPlacementNote";
import { formatDateTime, formatSlotPath, formatCategory, formatSize } from "@/lib/format";
import type { ApiSession } from "@/lib/api";
import type { Item, ItemPlacement } from "@/lib/types";

export const dynamic = "force-dynamic";

interface DetailData {
  item: Item | null;
  placements: ItemPlacement[];
  session: ApiSession;
  error: string | null;
}

async function loadItem(id: string): Promise<DetailData> {
  const session = await requireSession();
  try {
    const [item, placements] = await Promise.all([
      api.getItem(id, session),
      api.getItemPlacements(id, session).catch(() => []),
    ]);
    return { item, placements, session, error: null };
  } catch (err) {
    console.error("item load failed", err);
    return { item: null, placements: [], session, error: "无法加载物品详情" };
  }
}

export default async function ItemDetail({ params }: { params: { id: string } }) {
  const data = await loadItem(params.id);
  if (data.error && !data.item) {
    return (
      <div>
        <PageHeader title="物品详情" />
        <ErrorState title={data.error} />
      </div>
    );
  }
  if (!data.item) {
    notFound();
  }
  const item = data.item!;
  const placement = item.current_placement;
  // The placements endpoint returns the active row first (newest first), but
  // pick by predicate rather than by index so reordering it above cannot
  // silently attach 「移出」 to a closed row.
  const activePlacement = data.placements.find((p) => p.removed_at === null) ?? null;
  const activeId = activePlacement?.id ?? null;
  const activeNote = activePlacement?.note ?? null;
  return (
    <div className="space-y-6">
      <PageHeader
        title={item.name}
        description={
          [formatCategory(item.category), item.subcategory].filter(Boolean).join(" · ") ||
          undefined
        }
        actions={
          <Link href="/items" className="btn-ghost">
            ← 返回列表
          </Link>
        }
      />

      <div className="grid gap-4 md:grid-cols-3">
        <div className="card overflow-hidden md:col-span-1">
          {item.primary_image_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={item.primary_image_url}
              alt={item.name}
              className="aspect-square w-full object-cover"
            />
          ) : (
            <div className="grid aspect-square w-full place-items-center bg-ink-50 text-ink-300">
              <svg className="h-16 w-16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1">
                <path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5v-9Z" />
              </svg>
            </div>
          )}
        </div>
        <div className="card p-5 md:col-span-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">基本信息</h2>
          <dl className="mt-3 grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-ink-500">名称</dt>
              <dd className="font-medium text-ink-900">{item.name}</dd>
            </div>
            <div>
              <dt className="text-ink-500">分类</dt>
              <dd className="font-medium text-ink-900">
                {formatCategory(item.category)}
                {item.subcategory ? ` · ${item.subcategory}` : ""}
              </dd>
            </div>
            <div>
              <dt className="text-ink-500">尺寸</dt>
              <dd className="font-medium text-ink-900">{formatSize(item.estimated_size)}</dd>
            </div>
            <div>
              <dt className="text-ink-500">敏感</dt>
              <dd className="font-medium text-ink-900">{item.is_sensitive ? "是" : "否"}</dd>
            </div>
            <div className="col-span-2">
              <dt className="text-ink-500">当前放置</dt>
              <dd className="mt-1 flex items-center gap-2">
                <span className="badge-brand">
                  📍 {formatSlotPath(placement)}
                </span>
              </dd>
            </div>
            {item.description ? (
              <div className="col-span-2">
                <dt className="text-ink-500">描述</dt>
                <dd className="text-ink-800">{item.description}</dd>
              </div>
            ) : null}
          </dl>

          <div className="mt-5 flex flex-wrap items-center gap-2">
            <Link
              href={`/items/new?recommend_for=${item.id}`}
              className="btn-primary"
            >
              ✨ 获取推荐位置
            </Link>
            <PlaceItemButton item={item} session={data.session} label="放到别处" />
            {activeId ? (
              <RemovePlacementButton placementId={activeId} session={data.session} />
            ) : null}
            <Link href="/assistant" className="btn-secondary">
              AI 助手查找
            </Link>
          </div>
        </div>
      </div>

      <section className="card p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">摆放历史</h2>
        {data.placements.length === 0 ? (
          <p className="mt-3 text-sm text-ink-500">暂无摆放记录</p>
        ) : (
          <ol className="mt-3 space-y-2">
            {data.placements.map((p) => (
              <li
                key={p.id}
                className="flex items-center justify-between rounded-lg bg-ink-50 px-3 py-2"
              >
                <div>
                  <p className="text-sm text-ink-800">
                    {p.slot_path ?? `槽位 ${p.slot_id.slice(0, 8)}`}
                  </p>
                  <p className="text-xs text-ink-500">
                    {p.source === "ai_recommendation" ? "AI 推荐放置" : "手动放置"} ·{" "}
                    {formatDateTime(p.placed_at)}
                  </p>
                  {p.reason ? (
                    <p className="mt-1 text-xs text-ink-500">
                      为什么放这里：{p.reason}
                    </p>
                  ) : null}
                </div>
                <div className="flex flex-col items-end gap-1">
                  {p.removed_at ? (
                    <span className="badge">已结束 {formatDateTime(p.removed_at)}</span>
                  ) : (
                    <>
                      <span className="badge-brand">进行中</span>
                      {activeId ? (
                        <EditPlacementNote
                          placementId={activeId}
                          initialNote={activeNote}
                          session={data.session}
                        />
                      ) : null}
                    </>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
