import { Suspense } from "react";
import { PageHeader } from "@/components/PageHeader";
import { Loading } from "@/components/States";
import { requireSession } from "@/lib/session.server";
import { NewItemForm } from "./NewItemForm";

export const dynamic = "force-dynamic";

export default async function NewItemPage() {
  // The form is a client island, but the token must not be read from
  // `document.cookie` while rendering: the server pass would see `null` and
  // hydrate a different tree. Resolve the session here and pass it down.
  const session = await requireSession();
  return (
    <Suspense
      fallback={
        <div>
          <PageHeader title="添加物品" />
          <div className="card p-8">
            <Loading label="正在准备页面…" />
          </div>
        </div>
      }
    >
      <NewItemForm session={session} />
    </Suspense>
  );
}
