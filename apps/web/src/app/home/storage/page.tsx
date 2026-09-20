import { api } from "@/lib/api";
import { getSession } from "@/lib/session";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState } from "@/components/States";
import type { SpaceTree } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function StoragePage() {
  const session = getSession();
  let tree: SpaceTree | null = null;
  try {
    tree = await api.getSpaceTree(session.userId, session.homeId);
  } catch (err) {
    console.error("storage load failed", err);
  }
  if (!tree) {
    return (
      <div>
        <PageHeader title="收纳空间" />
        <ErrorState
          title="无法加载收纳空间"
          description="请确认后端 API 可用。"
        />
      </div>
    );
  }
  const totalSlots = tree.rooms.reduce(
    (acc, r) =>
      acc +
      (r.units ?? []).reduce(
        (a, u) => a + (u.sections ?? []).reduce((b, s) => b + (s.slots?.length ?? 0), 0),
        0,
      ),
    0,
  );
  return (
    <div className="space-y-6">
      <PageHeader
        title="收纳空间"
        description="柜子 → 层 → 格 的完整结构"
      />
      {totalSlots === 0 ? (
        <EmptyState
          title="还没有任何收纳空间"
          description="通过后端 API 创建房间、柜子、层和格子。"
        />
      ) : (
        <div className="space-y-4">
          {tree.rooms.map((room) => (
            <div key={room.id} className="card p-4">
              <h3 className="text-base font-semibold text-ink-900">{room.name}</h3>
              <div className="mt-3 space-y-3">
                {(room.units ?? []).map((unit) => (
                  <div
                    key={unit.id}
                    className="rounded-xl border border-ink-100 bg-ink-50/40 p-3"
                  >
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-semibold text-ink-800">{unit.name}</p>
                      <span className="text-xs text-ink-500">{unit.unit_type}</span>
                    </div>
                    <div className="mt-2 space-y-2 pl-3">
                      {(unit.sections ?? []).map((sec) => (
                        <div key={sec.id} className="rounded-lg bg-white p-2.5">
                          <p className="text-sm text-ink-700">{sec.name}</p>
                          <div className="mt-1.5 flex flex-wrap gap-1.5">
                            {(sec.slots ?? []).map((slot) => (
                              <span
                                key={slot.id}
                                className="badge font-mono text-[11px]"
                                title={slot.label ?? ""}
                              >
                                {slot.code}
                              </span>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
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
