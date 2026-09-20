import Link from "next/link";
import type { Item } from "@/lib/types";
import { formatRelative, formatSlotPath } from "@/lib/format";
import { clsx } from "@/lib/format";

export function ItemCard({ item, href }: { item: Item; href?: string }) {
  const content = (
    <div className="card flex gap-3 p-3 hover:border-brand-200 hover:shadow-soft transition-all">
      <Thumb url={item.primary_image_url} name={item.name} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h3 className="truncate text-sm font-semibold text-ink-900">{item.name}</h3>
          {item.is_sensitive ? <span className="badge">敏感</span> : null}
        </div>
        <p className="mt-0.5 truncate text-xs text-ink-500">
          {[item.category, item.subcategory].filter(Boolean).join(" · ") || "未分类"}
        </p>
        <p className="mt-1.5 truncate text-xs text-ink-600">
          📍 {formatSlotPath(item.current_placement as never)}
        </p>
        {item.created_at ? (
          <p className="mt-0.5 text-[11px] text-ink-400">添加于 {formatRelative(item.created_at)}</p>
        ) : null}
      </div>
    </div>
  );
  if (href) {
    return (
      <Link href={href} className="block">
        {content}
      </Link>
    );
  }
  return content;
}

function Thumb({ url, name }: { url?: string; name: string }) {
  if (url) {
    return (
      <div className="h-16 w-16 shrink-0 overflow-hidden rounded-xl bg-ink-100">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={url}
          alt={name}
          className="h-full w-full object-cover"
          loading="lazy"
        />
      </div>
    );
  }
  return (
    <div className="grid h-16 w-16 shrink-0 place-items-center rounded-xl bg-brand-50 text-brand-500">
      <svg className="h-7 w-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5v-9Z" />
        <path d="M3 7.5 12 12l9-4.5" />
      </svg>
    </div>
  );
}

export function ItemGrid({ items, empty }: { items: Item[]; empty?: React.ReactNode }) {
  if (items.length === 0 && empty) {
    return <>{empty}</>;
  }
  return (
    <div className={clsx("grid gap-3 sm:grid-cols-2 lg:grid-cols-3")}>
      {items.map((it) => (
        <ItemCard key={it.id} item={it} href={`/items/${it.id}`} />
      ))}
    </div>
  );
}
