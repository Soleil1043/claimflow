import Link from "next/link";
import { SUPPORT_ROLE_LABEL, formatTime, listSupportTickets } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * 客服工单列表（T135）：转人工（escalated）的门户客服会话队列。
 * 坐席进入详情回复客户 / 关闭会话。
 */
export default async function SupportTicketQueuePage() {
  let body: Awaited<ReturnType<typeof listSupportTickets>> | null = null;
  let error = "";
  try {
    body = await listSupportTickets();
  } catch (e) {
    error = String(e instanceof Error ? e.message : e);
  }

  const items = body?.items ?? [];

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-[26px] font-bold leading-tight tracking-tight">客服工单</h1>
          <p className="mt-1 text-[13px] text-cf-text-2">
            门户在线客服转人工的会话队列：坐席回复 · 关闭会话
          </p>
        </div>
        <div className="text-right text-[11px] text-cf-text-2">
          <div>共 {items.length} 条</div>
        </div>
      </header>

      {error && (
        <div className="cf-card border-cf-red/20 bg-cf-red/[0.06] px-4 py-3 text-[13px] text-[#B3261E]">
          后端不可达或返回错误:{error}
        </div>
      )}

      {body && items.length === 0 && (
        <div className="cf-card border-dashed py-16 text-center">
          <div className="text-[15px] font-medium text-cf-text">当前无转人工客服会话</div>
          <div className="mt-1 text-[12px] text-cf-text-2">
            门户客服对话中触发转人工的会话会自动进入此队列
          </div>
        </div>
      )}

      {items.length > 0 && (
        <div className="cf-card overflow-hidden p-0">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-black/[0.06] bg-black/[0.02] text-left text-[11px] tracking-wide text-cf-text-2">
                <th className="px-4 py-2.5 font-medium">会话</th>
                <th className="px-4 py-2.5 font-medium">转人工原因</th>
                <th className="px-4 py-2.5 font-medium">最新消息</th>
                <th className="px-4 py-2.5 font-medium">转入时间</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={item.conversation_id}
                  className="border-b border-black/[0.04] transition-colors duration-100 last:border-0 hover:bg-black/[0.02]"
                >
                  <td className="px-4 py-3 font-mono text-[12px] text-cf-text-2">
                    {item.conversation_id.slice(0, 10)}…
                  </td>
                  <td className="max-w-[220px] truncate px-4 py-3 text-cf-text-2" title={item.escalated_reason ?? ""}>
                    {item.escalated_reason ?? "-"}
                  </td>
                  <td className="max-w-[320px] truncate px-4 py-3 text-cf-text" title={item.last_message?.content ?? ""}>
                    {item.last_message
                      ? `${SUPPORT_ROLE_LABEL[item.last_message.role] ?? item.last_message.role}：${item.last_message.content}`
                      : "-"}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-[12px] tabular-nums text-cf-text-2">
                    {formatTime(item.escalated_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      href={`/support/${item.conversation_id}`}
                      className="cf-btn secondary cf-pressable"
                    >
                      处理 →
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
