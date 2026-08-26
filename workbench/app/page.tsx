import Link from "next/link";
import StatusBadge from "@/components/StatusBadge";
import { STATUS_TABS, formatTime, listTickets, type TicketStatus } from "@/lib/api";

export const dynamic = "force-dynamic";

const TAB_STYLE = {
  active: "border-sky-600 bg-white text-sky-700",
  inactive: "border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300",
};

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
    <main className="mx-auto max-w-5xl px-6 py-8">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold">人工介入工单</h1>
          <p className="mt-1 text-sm text-slate-500">
            合规拦截（REJECT）转人工的会话处理队列 — claimflow 坐席工作台
          </p>
        </div>
        <div className="text-right text-xs text-slate-400">
          <div>后端代理 → localhost:8000</div>
          <div>共 {body?.total ?? 0} 条</div>
        </div>
      </header>

      <nav className="mb-4 flex gap-1 border-b border-slate-200">
        {STATUS_TABS.map((tab) => {
          const active = tab.key === status;
          const href = tab.key === "" ? "/" : `/?status=${tab.key}`;
          return (
            <Link
              key={tab.label}
              href={href}
              className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${
                active ? TAB_STYLE.active : TAB_STYLE.inactive
              }`}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>

      {error && (
        <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700 ring-1 ring-red-200">
          后端不可达或返回错误：{error}
          <div className="mt-1 text-xs text-red-500">请确认后端已启动（uvicorn app.main:app --port 8000）</div>
        </div>
      )}

      {body && body.items.length === 0 && (
        <div className="rounded-md border border-dashed border-slate-300 py-16 text-center text-sm text-slate-400">
          当前筛选下暂无工单
        </div>
      )}

      {body && body.items.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-left text-xs text-slate-500">
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
                <tr key={ticket.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                  <td className="px-4 py-3 font-mono text-xs text-slate-500">
                    #{ticket.id}
                    <div className="mt-0.5 text-[10px] text-slate-400">
                      {ticket.conversation_id.slice(0, 8)}…
                    </div>
                  </td>
                  <td className="px-4 py-3">{ticket.user_id}</td>
                  <td className="max-w-[320px] truncate px-4 py-3 text-slate-600" title={ticket.intervention_reason ?? ""}>
                    {ticket.intervention_reason ?? "-"}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={ticket.status} />
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-500">
                    {formatTime(ticket.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      href={`/tickets/${ticket.id}`}
                      className="rounded-md border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-slate-100"
                    >
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
