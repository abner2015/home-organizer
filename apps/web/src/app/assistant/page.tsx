import { AssistantClient } from "./AssistantClient";
import { PageHeader } from "@/components/PageHeader";
import { requireSession } from "@/lib/session.server";

export const dynamic = "force-dynamic";

const SUGGESTIONS: string[] = [
  "我的数据线在哪里？",
  "我家所有的厨房用品？",
  "客厅装饰柜 L1 放了什么？",
  "我家还有没有电池？",
  "我的马克杯在哪？",
];

export default async function AssistantPage() {
  // The chat is a client island, but the token must never be read from
  // `document.cookie` at render time: the server pass would then see `null`
  // and hydrate a different tree. The session is resolved here and passed
  // down as a prop.
  const session = await requireSession();
  return (
    <div className="space-y-6">
      <PageHeader
        title="AI 收纳助手"
        description="用自然语言询问你的家，AI 会基于真实物品和位置回答"
      />
      <AssistantClient suggestions={SUGGESTIONS} session={session} />
    </div>
  );
}
