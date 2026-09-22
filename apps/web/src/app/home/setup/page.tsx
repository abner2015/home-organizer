import { Suspense } from "react";
import { PageHeader } from "@/components/PageHeader";
import { Loading } from "@/components/States";
import { requireSession } from "@/lib/session.server";
import { StructureBuilder } from "./StructureBuilder";

export const dynamic = "force-dynamic";

export default async function SetupPage() {
  // The builder is a client island, but the token must not be read from
  // `document.cookie` while rendering: the server pass would see `null` and
  // hydrate a different tree. Resolve the session here and pass it down — the
  // same rule every other client island in this app follows.
  const session = await requireSession();
  return (
    <Suspense
      fallback={
        <div>
          <PageHeader title="搭建我的家" />
          <div className="card p-8">
            <Loading label="正在准备页面…" />
          </div>
        </div>
      }
    >
      <StructureBuilder session={session} />
    </Suspense>
  );
}
