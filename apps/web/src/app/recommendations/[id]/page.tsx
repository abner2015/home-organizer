import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { getSession } from "@/lib/session";
import { PageHeader } from "@/components/PageHeader";
import { ErrorState } from "@/components/States";
import { RecommendationActions } from "./RecommendationActions";
import { formatConfidence, formatSlotPath } from "@/lib/format";
import type { RecommendResponse, SpaceTree, StorageUnit } from "@/lib/types";
import { RecommendationTree } from "@/components/RecommendationTree";

export const dynamic = "force-dynamic";

interface PageData {
  rec: RecommendResponse | null;
  units: StorageUnit[];
  error: string | null;
}

async function load(id: string): Promise<PageData> {
  const session = getSession();
  try {
    const rec = await api.getRecommendation(id, session.userId, session.homeId);
    let units: StorageUnit[] = [];
    try {
      const tree = await api.getSpaceTree(session.userId, session.homeId);
      units = tree.rooms.flatMap((room) => (room.units ?? []).map((u) => ({ ...u })));
    } catch {
      // best-effort
    }
    return { rec, units, error: null };
  } catch (err) {
    console.error("recommendation load failed", err);
    return { rec: null, units: [], error: "无法加载推荐" };
  }
}

export default async function RecommendationDetail({ params }: { params: { id: string } }) {
  const data = await load(params.id);
  if (data.error) {
    return (
      <div>
        <PageHeader title="AI 推荐" />
        <ErrorState title={data.error} />
      </div>
    );
  }
  if (!data.rec) notFound();
  const rec = data.rec;
  const recommended = rec.candidates.find((c) => c.is_recommended) ?? rec.candidates[0];

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI 推荐"
        description={recommended ? formatSlotPath(recommended as never) : "暂无候选"}
        actions={
          <Link href="/items" className="btn-ghost">
            ← 返回物品列表
          </Link>
        }
      />

      {rec.error ? (
        <div className="card border-amber-200 bg-amber-50/50 p-4 text-sm text-amber-800">
          ⚠️ {rec.error.message}
        </div>
      ) : null}

      <section className="grid gap-4 lg:grid-cols-3">
        <div className="card p-5 lg:col-span-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-brand-600">⭐ 推荐位置</p>
          <h2 className="mt-1 text-lg font-semibold text-ink-900">
            {recommended
              ? `${recommended.room_name} · ${recommended.unit_name} · ${recommended.section_name} · ${recommended.code}`
              : "—"}
          </h2>
          <p className="mt-2 text-sm text-ink-700">{recommended?.reason}</p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-ink-500">
            <span className="badge">置信度 {formatConfidence(recommended?.confidence)}</span>
            {recommended?.matched_rules && recommended.matched_rules.length > 0 ? (
              <span className="badge">匹配规则 {recommended.matched_rules.length}</span>
            ) : null}
            <span className="badge">候选 {rec.candidates.length}</span>
            <span className="badge">预过滤 {rec.pre_filter_count}</span>
            <span className="badge">后过滤 {rec.post_filter_count}</span>
            {rec.retry_count > 0 ? <span className="badge">重试 {rec.retry_count}</span> : null}
          </div>
          {rec.recommendation_id ? (
            <RecommendationActions
              recId={rec.recommendation_id}
              candidates={rec.candidates}
            />
          ) : null}
        </div>
        <div className="card p-5">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-ink-400">备选位置</h3>
          <ul className="mt-3 space-y-2">
            {rec.candidates
              .filter((c) => c !== recommended)
              .map((c) => (
                <li
                  key={c.slot_id}
                  className="rounded-lg bg-ink-50 p-3 text-sm"
                >
                  <p className="font-medium text-ink-800">
                    {c.room_name} · {c.unit_name} · {c.section_name} · {c.code}
                  </p>
                  <p className="mt-0.5 line-clamp-2 text-xs text-ink-500">{c.reason}</p>
                  <p className="mt-1 text-xs text-ink-400">
                    置信度 {formatConfidence(c.confidence)}
                  </p>
                </li>
              ))}
          </ul>
        </div>
      </section>

      {data.units.length > 0 ? (
        <section>
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-400">
            完整空间结构
          </h3>
          <RecommendationTree units={data.units} candidates={rec.candidates} />
        </section>
      ) : null}

      <p className="text-center text-xs text-ink-400">
        Trace ID: <span className="font-mono">{rec.trace_id}</span>
      </p>
    </div>
  );
}
