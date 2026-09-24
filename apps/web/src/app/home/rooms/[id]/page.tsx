import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { requireSession } from "@/lib/session.server";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState } from "@/components/States";
import type { Room } from "@/lib/types";

export const dynamic = "force-dynamic";

const ROOM_TYPE_LABEL: Record<string, string> = {
  bedroom: "卧室",
  kitchen: "厨房",
  living: "客厅",
  bathroom: "卫生间",
  study: "书房",
  storage: "储藏间",
  other: "其他",
};

async function loadRoom(id: string): Promise<{
  room: Room | null;
  units: Awaited<ReturnType<typeof api.listStorageUnits>> | null;
  error: string | null;
}> {
  const session = await requireSession();
  try {
    const room = await api.getRoom(id, session);
    const units = await api.listStorageUnits(id, session).catch(() => []);
    return { room, units, error: null };
  } catch (err) {
    console.error("room load failed", err);
    return { room: null, units: [], error: "无法加载房间详情" };
  }
}

export default async function RoomDetail({
  params,
}: {
  params: { id: string };
}) {
  const data = await loadRoom(params.id);
  if (data.error && !data.room) {
    return (
      <div>
        <PageHeader title="房间详情" />
        <ErrorState title={data.error} />
      </div>
    );
  }
  if (!data.room) {
    notFound();
  }
  const room = data.room!;
  const units = data.units ?? [];
  return (
    <div className="space-y-6">
      <PageHeader
        title={room.name}
        description={
          ROOM_TYPE_LABEL[room.room_type ?? "other"] ?? room.room_type
        }
        actions={
          <Link href="/home/rooms" className="btn-ghost">
            ← 返回 房间
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
            <dd className="font-medium text-ink-900">{room.name}</dd>
          </div>
          <div>
            <dt className="text-ink-500">类型</dt>
            <dd className="font-medium text-ink-900">
              {ROOM_TYPE_LABEL[room.room_type ?? "other"] ?? room.room_type}
            </dd>
          </div>
          <div>
            <dt className="text-ink-500">收纳家具</dt>
            <dd className="font-medium text-ink-900">{units.length} 件</dd>
          </div>
        </dl>
      </section>

      <section className="card p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
          收纳家具
        </h2>
        {units.length === 0 ? (
          <p className="mt-3 text-sm text-ink-500">该房间还没有任何收纳家具。</p>
        ) : (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {units.map((u) => (
              <Link
                key={u.id}
                href={`/home/storage/units/${u.id}`}
                className="rounded-xl border border-ink-100 bg-ink-50/40 p-3 transition-colors hover:bg-brand-50"
              >
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold text-ink-800">{u.name}</p>
                  <span className="text-xs text-ink-500">{u.unit_type}</span>
                </div>
                <p className="mt-1 text-xs text-ink-500">
                  {(u.sections ?? []).length} 层
                </p>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}