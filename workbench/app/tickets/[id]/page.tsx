import Link from "next/link";
import { notFound } from "next/navigation";
import MessageTimeline from "@/components/MessageTimeline";
import ResolveForm from "@/components/ResolveForm";
import StatusBadge from "@/components/StatusBadge";
import { formatTime, getTicket } from "@/lib/api";

export const dynamic = "force-dynamic";

function riskColor(score: number | undefined): string {
  if (score === undefined) return "text-slate-500";
  if (score >= 80) return "text-red-600";
  if (score >= 40) return "text-amber-600";
  return "text-emerald-600";
}

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
        <Link href="/" className="text-sm text-sky-700 hover:text-sky-900">
          ← 返回工单列表
        </Link>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <span>会话 {ticket.conversation_id.slice(0, 8)}…</span>
          <span>会话状态 {ticket.conversation.status}</span>
        </div>
      </div>

      <header className="mb-6 rounded-lg border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold">工单 #{ticket.id}</h1>
          <StatusBadge status={ticket.status} />
          <span className="ml-auto text-xs text-slate-400">
            创建 {formatTime(ticket.created_at)}
            {ticket.updated_at ? ` · 更新 ${formatTime(ticket.updated_at)}` : ""}
          </span>
        </div>
        <div className="mt-3 grid gap-2 text-sm md:grid-cols-2">
          <div>
            <span className="text-slate-400">用户：</span>
            {ticket.user_id}
          </div>
          <div>
            <span className="text-slate-400">拦截原因：</span>
            {ticket.intervention_reason ?? "-"}
          </div>
        </div>
      </header>

      {/* 合规拦截快照（转人工时刻的裁决证据） */}
      <section className="mb-6 rounded-lg border border-red-200 bg-red-50/40 p-5">
        <h2 className="mb-3 text-sm font-semibold text-red-800">
          ⚠ 合规拦截快照（compliance_snapshot）
        </h2>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <span className="rounded bg-red-600 px-2 py-0.5 text-xs font-bold text-white">
            {snapshot.verdict ?? "UNKNOWN"}
          </span>
          <span>
            风险分 <b className={`font-mono text-lg ${riskColor(snapshot.risk_score)}`}>{snapshot.risk_score ?? "-"}</b>
          </span>
          {snapshot.reason && <span className="text-slate-600">{snapshot.reason}</span>}
        </div>
        {violations.length > 0 && (
          <ul className="mt-3 space-y-1.5 text-sm">
            {violations.map((v, i) => (
              <li key={i} className="rounded border border-red-100 bg-white px-3 py-1.5">
                <span className="mr-2 rounded bg-red-100 px-1.5 py-0.5 font-mono text-xs text-red-700">
                  {v.type}
                </span>
                {v.detail}
                {v.suggestion && (
                  <div className="mt-1 text-xs text-slate-500">建议：{v.suggestion}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 坐席处理：pending 显示动作表单；终态显示已回写结论 */}
      <section className="mb-6 rounded-lg border border-slate-200 bg-white p-5">
        <h2 className="mb-3 text-sm font-semibold text-slate-700">坐席处理</h2>
        {ticket.status === "pending" ? (
          <ResolveForm ticketId={ticket.id} />
        ) : (
          <div className="space-y-1 text-sm">
            <div className="text-slate-500">
              {ticket.status === "resolved" ? "已解决" : "已升级转出"}（处理人 {ticket.resolved_by ?? "-"}）
            </div>
            <div className="whitespace-pre-wrap rounded-md bg-slate-50 px-3 py-2 text-slate-700 ring-1 ring-slate-100">
              {ticket.resolution_note ?? "（无结论记录）"}
            </div>
          </div>
        )}
      </section>

      {/* 会话完整轨迹（对话气泡 + 工具/Agent 审计展开） */}
      <section className="rounded-lg border border-slate-200 bg-white p-5">
        <h2 className="mb-4 text-sm font-semibold text-slate-700">
          会话轨迹（{ticket.messages.length} 条，含坐席回复）
        </h2>
        <MessageTimeline messages={ticket.messages} />
      </section>
    </main>
  );
}
