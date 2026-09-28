// P1.3 — proposals list page. Server component so the SSR cookie is already
// resolved into a Session; the inner `<ProposalsList>` is the only client
// island and just fires the accept / reject POSTs.
//
// We always show every status (no filter); the default-load behaviour from the
// API was the same. Filter chips let the user scope down without a round-trip
// for the common "only pending" view.

import { api } from "@/lib/api";
import { requireSession } from "@/lib/session.server";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState } from "@/components/States";
import type { StructureProposalRow } from "@/lib/types";
import { ProposalsList } from "./ProposalsList";

export const dynamic = "force-dynamic";

async function loadProposals(): Promise<StructureProposalRow[] | null> {
  const session = await requireSession();
  try {
    return await api.listProposals(session);
  } catch (err) {
    console.error("proposals load failed", err);
    return null;
  }
}

export default async function ProposalsPage() {
  const rows = await loadProposals();
  if (rows === null) {
    return (
      <div>
        <PageHeader title="结构提议" description="查看和管理 AI 提议过的家居结构" />
        <ErrorState title="无法连接到后端" description="请确认后端服务是否在运行。" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="结构提议"
        description="查看 AI 提议过的家居结构；可以接受让它成为真实房间 / 柜子，也可以写下理由拒绝。"
      />

      {rows.length === 0 ? (
        <EmptyState
          title="还没有任何提议"
          description="去搭建我的家（/home/setup）描述一下，或者先用模板搭一个骨架 —— 提议会出现在这里。"
        />
      ) : (
        <ProposalsList rows={rows} />
      )}
    </div>
  );
}
