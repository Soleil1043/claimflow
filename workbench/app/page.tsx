import Link from "next/link";
import StatusBadge from "@/components/StatusBadge";
import { STATUS_TABS, formatTime, listTickets, type TicketStatus } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * 工单列表（T057 / D030）：
 * - 状态筛选改 Apple 分段控件（segmented control），选中态白底浮起
 * - 表格承载于 cf-card，行 hover 反馈，详情按钮按压即时反馈
 */
export default async function TicketListPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const params = await searchParams;
  const status = (params.status ?? "") as TicketStatus | "";

  let body: Awaited<ReturnType<typeof listTickets>> | null = null;
  let error = "";
  try {
    body = await listTickets(status === "" ? undefined : status);
  } catch (e) {
    error = String(e instanceof Error ? e.message : e);
  }

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-[26px] font-bold leading-tight tracking-tight">人工介入工单</h1>
          <p className="mt-1 text-[13px] text-cf-text-2">
            合规拦截（REJECT）转人工的会话处理队列
          </p>
        </div>
        <div className="text-right text-[11px] text-cf-text-2">
          <div>共 {body?.total ?? 0} 条</div>
        </div>
      </header>

      {/* 分段控件：直接具体的标签，选中态白底浮起 */}
      <nav className="mb-5 inline-flex rounded-full bg-black/[0.05] p-1">
        {STATUS_TABS.map((tab) => {
          const active = tab.key === status;
          const href = tab.key === "" ? "/" : `/?status=${tab.key}`;
          return (
            <Link
              key={tab.label}
              href={href}
              className={`rounded-full px-4 py-1.5 text-[13px] font-medium transition-colors duration-150 ${
                active
                  ? "bg-white text-cf-text shadow-sm"
                  : "text-cf-text-2 hover:text-cf-text"
              }`}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>

      {error && (
        <div className="cf-card border-cf-red/20 bg-cf-red/[0.06] px-4 py-3 text-[13px] text-[#B3261E]">
          后端不可达或返回错误:{error}
          <div className="mt-1 text-[11px] opacity-70">
            请确认后端已启动（uvicorn app.main:app --port 8000）
          </div>
        </div>
      )}

      {body && body.items.length === 0 && (
        <div className="cf-card border-dashed py-16 text-center">
          <div className="text-[15px] font-medium text-cf-text">当前筛选下暂无工单</div>
          <div className="mt-1 text-[12px] text-cf-text-2">
            合规拦截 REJECT 的会话会自动进入此队列
          </div>
        </div>
      )}

      {body && body.items.length > 0 && (
        <div className="cf-card overflow-hidden p-0">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-black/[0.06] bg-black/[0.02] text-left text-[11px] tracking-wide text-cf-text-2">
                <th className="px-4 py-2.5 font-medium">工单</th>
                <th className="px-4 py-2.5 font-medium">用户</th>
                <th className="px-4 py-2.5 font-medium">拦截原因</th>
                <th className="px-4 py-2.5 font-medium">状态</th>
                <th className="px-4 py-2.5 font-medium">创建时间</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {body.items.map((ticket) => (
                <tr
                  key={ticket.id}
                  className="border-b border-black/[0.04] transition-colors duration-100 last:border-0 hover:bg-black/[0.02]"
                >
                  <td className="px-4 py-3 font-mono text-[12px] text-cf-text-2">
                    #{ticket.id}
                    <div className="mt-0.5 text-[10px] opacity-60">
                      {ticket.conversation_id.slice(0, 8)}…
                    </div>
                  </td>
                  <td className="px-4 py-3 text-cf-text">{ticket.user_id}</td>
                  <td
                    className="max-w-[320px] truncate px-4 py-3 text-cf-text-2"
                    title={ticket.intervention_reason ?? ""}
                  >
                    {ticket.intervention_reason ?? "-"}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={ticket.status} />
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-[12px] tabular-nums text-cf-text-2">
                    {formatTime(ticket.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link href={`/tickets/${ticket.id}`} className="cf-btn secondary cf-pressable">
                      查看详情 →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
