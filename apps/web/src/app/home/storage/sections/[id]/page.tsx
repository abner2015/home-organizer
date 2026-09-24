import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { requireSession } from "@/lib/session.server";
import { PageHeader } from "@/components/PageHeader";
import { ErrorState } from "@/components/States";
import type { StorageSection } from "@/lib/types";

export const dynamic = "force-dynamic";

const SECTION_TYPE_LABEL: Record<string, string> = {
  layer: "层",
  drawer: "抽屉",
  box: "盒",
  compartment: "分隔区",
  other: "其他",
};

async function loadSection(id: string): Promise<{
  section: StorageSection | null;
  parent: { unitId: string; unitName: string } | null;
  error: string | null;
}> {
  const session = await requireSession();
  try {
    const section = await api.getSection(id, session);
    // Walk up to the parent unit for the back-link label. The list of sections
    // / units / rooms comes from getSpaceTree (cheap; one round-trip).
    const tree = await api.getSpaceTree(session).catch(() => null);
    let parent: { unitId: string; unitName: string } | null = null;
    if (tree) {
      outer: for (const room of tree.rooms) {
        for (const u of room.units ?? []) {
          if ((u.sections ?? []).some((s) => s.id === section.id)) {
            parent = { unitId: u.id, unitName: u.name };
            break outer;
          }
        }
      }
    }
    return { section, parent, error: null };
  } catch (err) {
    console.error("section load failed", err);
    return { section: null, parent: null, error: "无法加载分层详情" };
  }
}

export default async function SectionDetail({
  params,
}: {
  params: { id: string };
}) {
  const data = await loadSection(params.id);
  if (data.error && !data.section) {
    return (
      <div>
        <PageHeader title="分层详情" />
        <ErrorState title={data.error} />
      </div>
    );
  }
  if (!data.section) {
    notFound();
  }
  const section = data.section!;
  const slots = section.slots ?? [];
  return (
    <div className="space-y-6">
      <PageHeader
        title={section.name}
        description={SECTION_TYPE_LABEL[section.section_type ?? "other"] ?? section.section_type}
        actions={
          data.parent ? (
            <Link
              href={`/home/storage/units/${data.parent.unitId}`}
              className="btn-ghost"
            >
              ← 返回 {data.parent.unitName}
            </Link>
          ) : (
            <Link href="/home/storage" className="btn-ghost">
              ← 返回 收纳空间
            </Link>
          )
        }
      />

      <section className="card p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
          基本信息
        </h2>
        <dl className="mt-3 grid grid-cols-2 gap-4 text-sm">
          <div>
            <dt className="text-ink-500">名称</dt>
            <dd className="font-medium text-ink-900">{section.name}</dd>
          </div>
          <div>
            <dt className="text-ink-500">类型</dt>
            <dd className="font-medium text-ink-900">
              {SECTION_TYPE_LABEL[section.section_type ?? "other"] ?? section.section_type}
            </dd>
          </div>
          <div>
            <dt className="text-ink-500">格子数</dt>
            <dd className="font-medium text-ink-900">{slots.length} 格</dd>
          </div>
        </dl>
      </section>

      <section className="card p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
          格子
        </h2>
        {slots.length === 0 ? (
          <p className="mt-3 text-sm text-ink-500">该分层还没有格子。</p>
        ) : (
          <div className="mt-3 flex flex-wrap gap-2">
            {slots.map((slot) => (
              <Link
                key={slot.id}
                href={`/home/storage/slots/${slot.id}`}
                className="badge font-mono text-[11px] transition-colors hover:bg-brand-100"
                title={slot.label ?? ""}
              >
                {slot.code}
                {(slot.active_count ?? 0) > 0 ? (
                  <span className="ml-1 inline-flex h-1.5 w-1.5 rounded-full bg-brand-500" />
                ) : null}
              </Link>
            ))}
          </div>
        )}
        <p className="mt-3 text-xs text-ink-500">
          带圆点的格子表示当前已经有物品。
        </p>
      </section>
    </div>
  );
}