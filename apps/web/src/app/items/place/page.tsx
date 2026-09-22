import { api } from "@/lib/api";
import { requireSession } from "@/lib/session.server";
import { PageHeader } from "@/components/PageHeader";
import { ErrorState } from "@/components/States";
import { BatchPlaceClient } from "@/components/placements/BatchPlaceClient";
import type { Item } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * 「批量归位」 — the shelf-first half of P0.3.
 *
 * The item list speaks per item; standing in front of a shelf you already know
 * the target, so this page asks for the slot first. Loaded in one page (200) —
 * a home with more items than that is truncated rather than paged, because the
 * alternative (paging inside a multi-select) loses ticks on every page change.
 */
export default async function BatchPlacePage() {
  const session = await requireSession();
  let items: Item[] = [];
  let error: string | null = null;
  try {
    const result = await api.listItems(session, { page: 1, page_size: 200 });
    items = result.items;
  } catch (err) {
    console.error("batch place load failed", err);
    error = "无法加载物品列表";
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="批量归位"
        description="先选一个位置，再勾选要放进这里的物品"
      />
      {error ? (
        <ErrorState title={error} description="请确认后端 API 可用。" />
      ) : (
        <BatchPlaceClient items={items} session={session} />
      )}
    </div>
  );
}
