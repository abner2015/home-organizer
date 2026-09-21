import Link from "next/link";
import { api } from "@/lib/api";
import { requireSession } from "@/lib/session.server";
import { PageHeader } from "@/components/PageHeader";
import { ItemGrid } from "@/components/Items";
import { EmptyState, ErrorState } from "@/components/States";
import { CATEGORY_LABEL } from "@/lib/format";
import type { Item } from "@/lib/types";

export const dynamic = "force-dynamic";

interface SearchParams {
  q?: string;
  category?: string;
}

export default async function ItemsPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const session = await requireSession();
  let items: Item[] = [];
  let total = 0;
  let error: string | null = null;
  try {
    const result = await api.listItems(session, {
      q: searchParams.q,
      category: searchParams.category,
      page: 1,
      page_size: 50,
    });
    items = result.items;
    total = result.total;
  } catch (err) {
    console.error("items load failed", err);
    error = "无法加载物品列表";
  }
  return (
    <div className="space-y-6">
      <PageHeader
        title="物品"
        description={total > 0 ? `共 ${total} 件` : "你的家庭物品清单"}
        actions={
          <Link href="/items/new" className="btn-primary">
            + 添加物品
          </Link>
        }
      />

      <form className="card flex flex-col gap-2 p-3 sm:flex-row" action="/items">
        <input
          name="q"
          defaultValue={searchParams.q ?? ""}
          placeholder="搜索物品名称…"
          className="input flex-1"
        />
        <select
          name="category"
          defaultValue={searchParams.category ?? ""}
          className="input sm:w-48"
        >
          <option value="">全部分类</option>
          {Object.entries(CATEGORY_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
          {searchParams.category && !(searchParams.category in CATEGORY_LABEL) ? (
            <option value={searchParams.category}>{searchParams.category}</option>
          ) : null}
        </select>
        <button className="btn-primary" type="submit">
          搜索
        </button>
      </form>

      {error ? (
        <ErrorState title={error} description="请确认后端 API 可用。" />
      ) : items.length === 0 ? (
        <EmptyState
          title="还没有任何物品"
          description="点击下方按钮添加第一件物品"
          action={
            <Link href="/items/new" className="btn-primary">
              + 添加物品
            </Link>
          }
        />
      ) : (
        <ItemGrid items={items} />
      )}
    </div>
  );
}
