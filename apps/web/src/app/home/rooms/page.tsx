import Link from "next/link";
import { api } from "@/lib/api";
import { requireSession } from "@/lib/session.server";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState } from "@/components/States";
import type { SpaceTree } from "@/lib/types";

export const dynamic = "force-dynamic";

const ROOM_TYPE_LABEL: Record<string, string> = {
  bedroom: "卧室",
  kitchen: "厨房",
  living: "客厅",
  bathroom: "卫生间",
  study: "书房",
  garage: "车库",
  other: "其他",
};

export default async function RoomsPage() {
  const session = await requireSession();
  let tree: SpaceTree | null = null;
  try {
    tree = await api.getSpaceTree(session);
  } catch (err) {
    console.error("rooms load failed", err);
  }
  if (!tree) {
    return (
      <div>
        <PageHeader title="房间" />
        <ErrorState
          title="无法加载房间列表"
          description="请确认后端 API 可用。"
        />
      </div>
    );
  }
  return (
    <div className="space-y-6">
      <PageHeader
        title="房间"
        description="按房间组织收纳空间"
        actions={
          <Link href="/home/storage" className="btn-secondary">
            收纳空间
          </Link>
        }
      />
      {tree.rooms.length === 0 ? (
        <EmptyState
          title="还没有任何房间"
          description="在管理收纳空间页面创建你的第一个房间。"
          action={
            <Link href="/home/storage" className="btn-primary">
              前往管理
            </Link>
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {tree.rooms.map((room) => (
            <div key={room.id} className="card p-4">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="font-semibold text-ink-900">{room.name}</h3>
                  <p className="text-xs text-ink-500">
                    {ROOM_TYPE_LABEL[room.room_type ?? "other"] ?? room.room_type}
                  </p>
                </div>
                <span className="badge-brand">{room.units?.length ?? 0} 个柜子</span>
              </div>
              <div className="mt-3 space-y-1.5">
                {(room.units ?? []).map((u) => (
                  <div
                    key={u.id}
                    className="flex items-center justify-between rounded-lg bg-ink-50 px-3 py-2"
                  >
                    <span className="text-sm text-ink-800">{u.name}</span>
                    <span className="text-xs text-ink-500">
                      {(u.sections?.length ?? 0)} 层
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
