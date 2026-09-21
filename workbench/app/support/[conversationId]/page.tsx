import Link from "next/link";
import SupportReplyForm from "@/components/SupportReplyForm";
import { SUPPORT_ROLE_LABEL, formatTime, getSupportTicket } from "@/lib/api";

export const dynamic = "force-dynamic";

const ROLE_BUBBLE: Record<string, string> = {
  user: "bg-cf-blue/[0.08] ring-cf-blue/15",
  assistant: "bg-black/[0.04] ring-black/[0.06]",
  agent: "bg-cf-green/[0.1] ring-cf-green/20",
};

/**
 * 客服工单详情（T135）：完整 transcript + 坐席回复表单（escalated）/
 * 关闭横幅（closed，只读审计）。
 */
export default async function SupportTicketDetailPage({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { conversationId } = await params;

  let ticket: Awaited<ReturnType<typeof getSupportTicket>> | null = null;
  let error = "";
  try {
    ticket = await getSupportTicket(conversationId);
  } catch (e) {
    error = String(e instanceof Error ? e.message : e);
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <Link href="/support" className="text-[12px] text-cf-text-2 hover:text-cf-text">
        ← 返回客服工单列表
      </Link>

      {error && (
        <div className="cf-card mt-4 border-cf-red/20 bg-cf-red/[0.06] px-4 py-3 text-[13px] text-[#B3261E]">
          加载失败:{error}
        </div>
      )}

      {ticket && (
        <>
          <header className="mt-4 mb-5">
            <div className="flex items-center gap-3">
              <h1 className="font-mono text-[18px] font-semibold tracking-tight">
                {ticket.conversation_id.slice(0, 14)}…
              </h1>
              <span
                className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium ring-1 ${
                  ticket.status === "escalated"
                    ? "bg-[#FF9500]/[0.12] text-[#8a5300] ring-[#FF9500]/25"
                    : "bg-black/[0.04] text-cf-text-2 ring-black/[0.06]"
                }`}
              >
                {ticket.status === "escalated" ? "转人工中" : "已关闭"}
              </span>
            </div>
            <p className="mt-1 text-[12px] text-cf-text-2">
              转人工原因：{ticket.escalated_reason ?? "-"} · 转入
              {formatTime(ticket.escalated_at)}
              {ticket.closed_at ? ` · 关闭${formatTime(ticket.closed_at)}` : ""}
            </p>
          </header>

          <section className="cf-card mb-5 p-4">
            <div className="space-y-2.5">
              {ticket.messages.map((m) => (
                <div key={m.id} className="flex flex-col">
                  <div className="mb-0.5 flex items-baseline gap-2">
                    <span className="text-[11px] font-medium text-cf-text-2">
                      {SUPPORT_ROLE_LABEL[m.role] ?? m.role}
                    </span>
                    <span className="text-[10px] tabular-nums text-cf-text-2/70">
                      {formatTime(m.created_at)}
                    </span>
                  </div>
                  <div
                    className={`whitespace-pre-wrap rounded-[10px] px-3 py-2 text-[13px] leading-relaxed ring-1 ${
                      ROLE_BUBBLE[m.role] ?? ROLE_BUBBLE.assistant
                    }`}
                  >
                    {m.content}
                  </div>
                </div>
              ))}
            </div>
          </section>

          {ticket.status === "escalated" ? (
            <SupportReplyForm conversationId={ticket.conversation_id} />
          ) : (
            <div className="rounded-[10px] bg-black/[0.03] px-3 py-2.5 text-[12px] text-cf-text-2">
              会话已关闭（终态）：transcript 只读留档，门户侧不可再发送消息。
            </div>
          )}
        </>
      )}
    </main>
  );
}
