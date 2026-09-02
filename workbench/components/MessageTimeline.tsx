import AuditViewer from "@/components/AuditViewer";
import { formatTime, type MessageItem } from "@/lib/api";

/** 合规裁决 pill（四态语义色，T057 / D030）。 */
const VERDICT_PILL: Record<string, string> = {
  REJECT: "err",
  MODIFY: "warn",
  MODIFIED: "warn",
  PASS: "ok",
};

/**
 * 会话完整轨迹：user/assistant 气泡（18px 连续圆角 + 尾侧小角）+
 * assistant 消息的审计展开（工具/Agent 步骤）。
 */
export default function MessageTimeline({ messages }: { messages: MessageItem[] }) {
  if (messages.length === 0) {
    return <div className="py-8 text-center text-[13px] text-cf-text-2">会话暂无消息</div>;
  }
  return (
    <div className="space-y-4">
      {messages.map((m) => (
        <div key={m.id} className={`flex ${m.role === "user" ? "justify-start" : "justify-end"}`}>
          <div
            className={`max-w-[85%] rounded-[18px] px-4 py-2.5 text-[13px] leading-relaxed shadow-sm ${
              m.role === "user"
                ? "rounded-tl-md bg-white text-cf-text ring-1 ring-black/[0.06]"
                : "rounded-tr-md bg-cf-blue/[0.09] text-cf-text ring-1 ring-cf-blue/[0.12]"
            }`}
          >
            <div className="mb-1 flex items-center gap-2 text-[11px] text-cf-text-2">
              <span className="font-medium">{m.role === "user" ? "用户" : "助手"}</span>
              {m.intent && <span className="cf-pill info">{m.intent}</span>}
              {m.compliance_status && (
                <span className={`cf-pill ${VERDICT_PILL[m.compliance_status] ?? "muted"}`}>
                  {m.compliance_status}
                </span>
              )}
              <span className="tabular-nums">{formatTime(m.created_at)}</span>
            </div>
            <div className="whitespace-pre-wrap">{m.content}</div>
            {m.role === "assistant" && (
              <AuditViewer toolTrace={m.tool_trace} agentSteps={m.agent_steps} />
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
