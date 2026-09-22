"use client";

import { useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { clsx } from "@/lib/format";
import type { ApiSession } from "@/lib/api";
import { ProposalFlow } from "./ProposalFlow";
import { ManualBuilder } from "./ManualBuilder";

type Mode = "ai" | "manual";

/**
 * 「搭建我的家」— the two ways to get a storage structure into the system.
 *
 * Before this page the only way was `python -m app.db.seed`, so a new account
 * owned an empty tree and every recommendation answered `state="failed"`. Both
 * modes end in the same place: ordinary `POST` calls on the write API, one node
 * at a time. The AI mode just decides *what* to post; it never posts anything
 * itself.
 */
export function StructureBuilder({ session }: { session: ApiSession }) {
  const [mode, setMode] = useState<Mode>("ai");

  return (
    <div>
      <PageHeader
        title="搭建我的家"
        description="描述一次、拍张照片，或者自己一级一级搭起来"
      />

      <div className="mb-5 inline-flex rounded-xl border border-ink-100 bg-white p-1">
        <TabButton active={mode === "ai"} onClick={() => setMode("ai")}>
          AI 帮我搭
        </TabButton>
        <TabButton active={mode === "manual"} onClick={() => setMode("manual")}>
          手动搭建
        </TabButton>
      </div>

      {mode === "ai" ? (
        <ProposalFlow session={session} />
      ) : (
        <ManualBuilder session={session} />
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "rounded-lg px-4 py-1.5 text-sm font-medium transition-colors",
        active ? "bg-brand-500 text-white" : "text-ink-600 hover:bg-ink-50",
      )}
    >
      {children}
    </button>
  );
}
