import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { requireSession } from "@/lib/session.server";
import { PageHeader } from "@/components/PageHeader";
import { ErrorState } from "@/components/States";
import type { StorageUnit } from "@/lib/types";

export const dynamic = "force-dynamic";

const UNIT_TYPE_LABEL: Record<string, string> = {
  cabinet: "柜子",
  shelf: "架子",
  drawer_cabinet: "抽屉柜",
  box: "收纳盒",
  other: "其他",
};

async function loadUnit(id: string): Promise<{
  unit: StorageUnit | null;
  error: string | null;
}> {
  const session = await requireSession();
  try {
    const unit = await api.getStorageUnit(id, session);
    return { unit, error: null };
  } catch (err) {
    console.error("storage unit load failed", err);
    return { unit: null, error: "无法加载收纳家具" };
  }
}

export default async function UnitDetail({
  params,
}: {
  params: { id: string };
}) {
  const data = await loadUnit(params.id);
  if (data.error && !data.unit) {
    return (
      <div>
        <PageHeader title="收纳家具详情" />
        <ErrorState title={data.error} />
      </div>
    );
  }
  if (!data.unit) {
    notFound();
  }
  const unit = data.unit!;
  const sections = unit.sections ?? [];
  return (
    <div className="space-y-6">
      <PageHeader
        title={unit.name}
        description={UNIT_TYPE_LABEL[unit.unit_type ?? "other"] ?? unit.unit_type}
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
            <dt className="text-ink-500">名称</dt>
            <dd className="font-medium text-ink-900">{unit.name}</dd>
          </div>
          <div>
            <dt className="text-ink-500">类型</dt>
            <dd className="font-medium text-ink-900">
              {UNIT_TYPE_LABEL[unit.unit_type ?? "other"] ?? unit.unit_type}
            </dd>
          </div>
          <div>
            <dt className="text-ink-500">层 / 抽屉</dt>
            <dd className="font-medium text-ink-900">{sections.length} 个</dd>
          </div>
        </dl>
      </section>

      <section className="card p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
          层 / 抽屉
        </h2>
        {sections.length === 0 ? (
          <p className="mt-3 text-sm text-ink-500">该收纳家具还没有分层。</p>
        ) : (
          <div className="mt-3 space-y-2">
            {sections.map((sec) => (
              <Link
                key={sec.id}
                href={`/home/storage/sections/${sec.id}`}
                className="block rounded-xl border border-ink-100 bg-ink-50/40 p-3 transition-colors hover:bg-brand-50"
              >
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold text-ink-800">{sec.name}</p>
                  <span className="text-xs text-ink-500">{sec.section_type}</span>
                </div>
                <p className="mt-1 text-xs text-ink-500">
                  {(sec.slots ?? []).length} 格
                </p>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}