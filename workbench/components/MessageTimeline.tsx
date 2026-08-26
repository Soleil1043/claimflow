import AuditViewer from "@/components/AuditViewer";
import { formatTime, type MessageItem } from "@/lib/api";

const VERDICT_STYLE: Record<string, string> = {
  REJECT: "bg-red-100 text-red-800",
  MODIFY: "bg-amber-100 text-amber-800",
  MODIFIED: "bg-amber-100 text-amber-800",
  PASS: "bg-emerald-100 text-emerald-800",
};

/**
 * 会话完整轨迹：user/assistant 气泡 + assistant 消息的审计展开（工具/Agent 步骤）。
 */
export default function MessageTimeline({ messages }: { messages: MessageItem[] }) {
  if (messages.length === 0) {
    return <div className="py-8 text-center text-sm text-slate-400">会话暂无消息</div>;
  }
  return (
    <div className="space-y-4">
      {messages.map((m) => (
        <div key={m.id} className={`flex ${m.role === "user" ? "justify-start" : "justify-end"}`}>
          <div
            className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
              m.role === "user"
                ? "rounded-tl-sm bg-white text-slate-800 ring-1 ring-slate-200"
                : "rounded-tr-sm bg-sky-50 text-slate-800 ring-1 ring-sky-100"
            }`}
          >
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <span className="font-medium">{m.role === "user" ? "用户" : "助手"}</span>
              {m.intent && (
                <span className="rounded bg-violet-100 px-1.5 py-0.5 text-violet-700">{m.intent}</span>
              )}
              {m.compliance_status && (
                <span
                  className={`rounded px-1.5 py-0.5 ${
                    VERDICT_STYLE[m.compliance_status] ?? "bg-slate-100 text-slate-600"
                  }`}
                >
                  {m.compliance_status}
                </span>
              )}
              <span>{formatTime(m.created_at)}</span>
            </div>
            <div className="whitespace-pre-wrap">{m.content}</div>
            {m.role === "assistant" && <AuditViewer toolTrace={m.tool_trace} agentSteps={m.agent_steps} />}
          </div>
        </div>
      ))}
    </div>
  );
}
