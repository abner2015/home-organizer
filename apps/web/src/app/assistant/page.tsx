import { AssistantClient } from "./AssistantClient";
import { PageHeader } from "@/components/PageHeader";

export const dynamic = "force-dynamic";

const SUGGESTIONS: string[] = [
  "我的数据线在哪里？",
  "我家所有的厨房用品？",
  "客厅装饰柜 L1 放了什么？",
  "我家还有没有电池？",
  "我的马克杯在哪？",
];

export default function AssistantPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="AI 收纳助手"
        description="用自然语言询问你的家，AI 会基于真实物品和位置回答"
      />
      <AssistantClient suggestions={SUGGESTIONS} />
    </div>
  );
}
