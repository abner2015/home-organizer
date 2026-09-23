import { PageHeader } from "@/components/PageHeader";
import { requireSession } from "@/lib/session.server";
import { CreateHomeForm } from "./CreateHomeForm";

// Force-dynamic: the page reads the access token from cookies, which the
// static renderer cannot do (and which would also let a logged-out visitor
// see the form only to crash on submit).
export const dynamic = "force-dynamic";

export default async function NewHomePage() {
  // Resolve the session server-side for the same reason every other client
  // island in this app reads it: reading `document.cookie` during render
  // makes the server pass see `null` and the client pass see the cookie,
  // hydration mismatch.
  const session = await requireSession();
  return (
    <div>
      <PageHeader
        title="新建一个家"
        description="每个家都是独立的收纳空间 —— 老家、新家、工作室可以分开管理"
      />
      <CreateHomeForm session={session} />
    </div>
  );
}
