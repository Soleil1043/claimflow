import Link from "next/link";
import { notFound } from "next/navigation";
import MessageTimeline from "@/components/MessageTimeline";
import ResolveForm from "@/components/ResolveForm";
import StatusBadge from "@/components/StatusBadge";
import { formatTime, getTicket } from "@/lib/api";

export const dynamic = "force-dynamic";

/** 风险分语义色（≥80 红 / ≥40 橙 / 其余绿，Apple 系统色）。 */
function riskColor(score: number | undefined): string {
  if (score === undefined) return "text-cf-text-2";
  if (score >= 80) return "text-cf-red";
  if (score >= 40) return "text-cf-orange";
  return "text-cf-green";
}

/**
 * 工单详情（T057 / D030）：
 * - 头部 cf-card：标题紧排 + 状态 pill + 时间戳
 * - 合规快照：风险分大数字（tabular-nums 负 tracking）+ verdict 印章 + 违规条目卡
 * - 坐席处理 / 会话轨迹分区卡片化，层级 = 字重 + 间距
 */
export default async function TicketDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let ticket;
  try {
    ticket = await getTicket(id);
  } catch {
    notFound();
  }

  const snapshot = ticket.compliance_snapshot ?? {};
  const violations = snapshot.violations ?? [];

  return (
    <main className="mx-auto max-w-4xl px-6 py-8">
      <div className="mb-4 flex items-center justify-between">
        <Link
          href="/"
          className="text-[13px] font-medium text-cf-blue transition-colors hover:text-cf-blue-dark"
        >
          ← 返回工单列表
        </Link>
        <div className="flex items-center gap-2 text-[11px] text-cf-text-2">
          <span>会话 {ticket.conversation_id.slice(0, 8)}…</span>
          <span>会话状态 {ticket.conversation.status}</span>
        </div>
      </div>

      <header className="cf-card mb-5 p-5">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-[22px] font-bold leading-tight tracking-tight">
            工单 #{ticket.id}
          </h1>
          <StatusBadge status={ticket.status} />
          <span className="ml-auto text-[11px] tabular-nums text-cf-text-2">
            创建 {formatTime(ticket.created_at)}
            {ticket.updated_at ? ` · 更新 ${formatTime(ticket.updated_at)}` : ""}
          </span>
        </div>
        <div className="mt-3 grid gap-2 text-[13px] md:grid-cols-2">
          <div>
            <span className="text-cf-text-2">用户：</span>
            {ticket.user_id}
          </div>
          <div>
            <span className="text-cf-text-2">拦截原因：</span>
            {ticket.intervention_reason ?? "-"}
          </div>
        </div>
      </header>

      {/* 合规拦截快照（转人工时刻的裁决证据） */}
      <section className="cf-card mb-5 border-cf-red/15 bg-cf-red/[0.04] p-5">
        <h2 className="mb-3 text-[13px] font-semibold text-[#B3261E]">
          ⚠ 合规拦截快照（compliance_snapshot）
        </h2>
        <div className="flex flex-wrap items-center gap-4 text-[13px]">
          <span className="rounded-lg bg-cf-red px-2.5 py-1 text-[11px] font-bold tracking-wide text-white">
            {snapshot.verdict ?? "UNKNOWN"}
          </span>
          <span className="flex items-baseline gap-1.5">
            风险分
            <b
              className={`font-mono text-[26px] font-bold leading-none tracking-tight tabular-nums ${riskColor(snapshot.risk_score)}`}
            >
              {snapshot.risk_score ?? "-"}
            </b>
          </span>
          {snapshot.reason && <span className="text-cf-text-2">{snapshot.reason}</span>}
        </div>
        {violations.length > 0 && (
          <ul className="mt-3 space-y-1.5 text-[13px]">
            {violations.map((v, i) => (
              <li
                key={i}
                className="rounded-[10px] border border-cf-red/10 bg-white px-3 py-2 shadow-sm"
              >
                <span className="mr-2 rounded bg-cf-red/10 px-1.5 py-0.5 font-mono text-[11px] text-[#B3261E]">
                  {v.type}
                </span>
                {v.detail}
                {v.suggestion && (
                  <div className="mt-1 text-[12px] text-cf-text-2">建议:{v.suggestion}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 坐席处理：pending 显示动作表单；终态显示已回写结论 */}
      <section className="cf-card mb-5 p-5">
        <h2 className="mb-3 text-[13px] font-semibold text-cf-text">坐席处理</h2>
        {ticket.status === "pending" ? (
          <ResolveForm ticketId={ticket.id} />
        ) : (
          <div className="space-y-2 text-[13px]">
            <div className="text-cf-text-2">
              {ticket.status === "resolved" ? "已解决" : "已升级转出"}（处理人{" "}
              {ticket.resolved_by ?? "-"}）
            </div>
            <div className="whitespace-pre-wrap rounded-[10px] bg-black/[0.03] px-3 py-2 text-cf-text ring-1 ring-black/[0.04]">
              {ticket.resolution_note ?? "（无结论记录）"}
            </div>
          </div>
        )}
      </section>

      {/* 会话完整轨迹（对话气泡 + 工具/Agent 审计展开） */}
      <section className="cf-card p-5">
        <h2 className="mb-4 text-[13px] font-semibold text-cf-text">
          会话轨迹（{ticket.messages.length} 条，含坐席回复）
        </h2>
        <MessageTimeline messages={ticket.messages} />
      </section>
    </main>
  );
}
