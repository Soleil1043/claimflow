"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { closeSupportTicket, replySupportTicket } from "@/lib/api";

/**
 * 客服工单处理表单（T135）：坐席回复（role=agent 入 transcript，门户轮询可见）
 * 与关闭会话（关闭备注作为最后一条坐席消息，此后终态只读）。
 */
export default function SupportReplyForm({
  conversationId,
}: {
  conversationId: string;
}) {
  const router = useRouter();
  const [agent, setAgent] = useState("");
  const [content, setContent] = useState("");
  const [closeNote, setCloseNote] = useState("");
  const [busy, setBusy] = useState<"reply" | "close" | null>(null);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  const agentOk = agent.trim().length > 0;

  async function onReply() {
    setBusy("reply");
    setError("");
    setInfo("");
    try {
      await replySupportTicket(conversationId, {
        agent: agent.trim(),
        content: content.trim(),
      });
      setContent("");
      setInfo("已回复，门户客服窗口将轮询到该消息");
      router.refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }

  async function onClose() {
    setBusy("close");
    setError("");
    setInfo("");
    try {
      await closeSupportTicket(conversationId, {
        agent: agent.trim(),
        note: closeNote.trim() || null,
      });
      setInfo("会话已关闭");
      router.refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="cf-card space-y-4 p-4">
      <label className="block">
        <span className="mb-1 block text-[12px] font-medium text-cf-text-2">坐席标识</span>
        <input
          value={agent}
          onChange={(e) => setAgent(e.target.value)}
          maxLength={64}
          placeholder="agent-01"
          className="cf-input max-w-[220px]"
        />
      </label>

      <label className="block">
        <span className="mb-1 block text-[12px] font-medium text-cf-text-2">
          回复客户（门户客服窗口轮询展示）
        </span>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={3}
          maxLength={4000}
          placeholder="例：您好，我是人工坐席。已核实您的案件进度：……"
          className="cf-input"
        />
      </label>
      <button
        type="button"
        disabled={!agentOk || content.trim().length === 0 || busy !== null}
        onClick={onReply}
        className="cf-btn primary"
      >
        {busy === "reply" ? "发送中…" : "发送回复"}
      </button>

      <div className="border-t border-black/[0.06] pt-4">
        <label className="block">
          <span className="mb-1 block text-[12px] font-medium text-cf-text-2">
            关闭备注（可选，作为最后一条坐席消息留档）
          </span>
          <textarea
            value={closeNote}
            onChange={(e) => setCloseNote(e.target.value)}
            rows={2}
            maxLength={4000}
            placeholder="例：问题已在线解决，客户确认满意。"
            className="cf-input"
          />
        </label>
        <button
          type="button"
          disabled={!agentOk || busy !== null}
          onClick={onClose}
          className="cf-btn secondary mt-3"
        >
          {busy === "close" ? "关闭中…" : "关闭会话（终态）"}
        </button>
      </div>

      {error && (
        <div className="rounded-[10px] bg-cf-red/[0.08] px-3 py-2 text-[13px] text-[#B3261E] ring-1 ring-cf-red/15">
          操作失败:{error}
        </div>
      )}
      {info && (
        <div className="rounded-[10px] bg-cf-green/[0.08] px-3 py-2 text-[13px] text-[#1F7A38] ring-1 ring-cf-green/20">
          ✓ {info}
        </div>
      )}
    </section>
  );
}
