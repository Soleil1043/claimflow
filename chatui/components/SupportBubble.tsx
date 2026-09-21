"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  createSupportConversation,
  getSupportConversation,
  listSupportMessages,
  sendSupportMessage,
  type SupportMessage,
  type SupportStatus,
} from "@/lib/support-api";

/** 会话 id 在浏览器侧的持久化键（跨刷新恢复客服对话）。 */
const STORAGE_KEY = "cf_support_conversation_id";

const ROLE_LABEL: Record<string, string> = {
  assistant: "AI 客服",
  agent: "人工客服",
};

/**
 * 门户悬浮在线客服（T136，D057）：右下角气泡 + 对话窗。
 *
 * - ai 态：发消息 → 请求-响应式 AI 回复（v1 无流式，"正在输入"态过渡，D057-3）
 * - escalated 态：AI 停答；打开期间 3s 轮询坐席回复 / 关闭事件
 * - closed 态：终态只读，可一键开新会话
 * - AI 消息以 Markdown 渲染（claim_draft_link 预填链接可直接点击跳转表单）
 */
export default function SupportBubble() {
  const [open, setOpen] = useState(false);
  const [convId, setConvId] = useState<string | null>(null);
  const [status, setStatus] = useState<SupportStatus>("ai");
  const [messages, setMessages] = useState<SupportMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const convRef = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const refresh = useCallback(async (id: string) => {
    const [conv, history] = await Promise.all([
      getSupportConversation(id),
      listSupportMessages(id),
    ]);
    setStatus(conv.status);
    setMessages(history.items);
  }, []);

  const ensureConversation = useCallback(async () => {
    if (convRef.current) return convRef.current;
    setLoading(true);
    setError("");
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        try {
          await refresh(stored);
          convRef.current = stored;
          setConvId(stored);
          return stored;
        } catch {
          localStorage.removeItem(STORAGE_KEY); // 后端已无此会话（如清库）→ 新建
        }
      }
      const conv = await createSupportConversation();
      convRef.current = conv.conversation_id;
      setConvId(conv.conversation_id);
      setStatus("ai");
      setMessages([]);
      localStorage.setItem(STORAGE_KEY, conv.conversation_id);
      return conv.conversation_id;
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
      return null;
    } finally {
      setLoading(false);
    }
  }, [refresh]);

  // escalated：坐席可能回复 / 关闭 —— 打开期间 3s 轮询（v1 无 SSE，D057-3）
  useEffect(() => {
    if (!open || !convId || status !== "escalated") return;
    const timer = setInterval(() => {
      refresh(convId).catch(() => {
        /* 单次轮询失败忽略，下一轮重试 */
      });
    }, 3000);
    return () => clearInterval(timer);
  }, [open, convId, status, refresh]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  async function onToggle() {
    const next = !open;
    setOpen(next);
    if (next) await ensureConversation();
  }

  function startNew() {
    convRef.current = null;
    setConvId(null);
    setMessages([]);
    setStatus("ai");
    setError("");
    localStorage.removeItem(STORAGE_KEY);
    void ensureConversation();
  }

  async function onSend() {
    const id = convRef.current;
    if (!id || !input.trim() || sending || status === "closed") return;
    const content = input.trim();
    setInput("");
    // 乐观追加用户消息（服务端是时间线单源，完成后整体刷新校准）
    setMessages((prev) => [
      ...prev,
      {
        id: -Date.now(),
        role: "user",
        content,
        created_at: new Date().toISOString(),
      },
    ]);
    setSending(true);
    setError("");
    try {
      await sendSupportMessage(id, content);
      await refresh(id);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setSending(false);
    }
  }

  return (
    <>
      {open && (
        <section className="cf-card fixed bottom-24 right-4 z-50 flex h-[min(70vh,560px)] w-[min(92vw,384px)] flex-col overflow-hidden p-0 shadow-2xl">
          <header className="flex items-center justify-between border-b border-black/[0.06] bg-black/[0.02] px-4 py-3">
            <div>
              <div className="text-[14px] font-semibold text-cf-text">在线客服</div>
              <div className="text-[11px] text-cf-text-2">
                {status === "ai" && "AI 客服为您服务 · 可随时要求转人工"}
                {status === "escalated" && "已转人工客服，坐席处理中…"}
                {status === "closed" && "会话已结束"}
              </div>
            </div>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded-full px-2 py-1 text-[13px] text-cf-text-2 transition-colors hover:bg-black/[0.05] hover:text-cf-text"
              aria-label="收起客服窗口"
            >
              ✕
            </button>
          </header>

          {status === "closed" && (
            <div className="flex items-center justify-between gap-2 border-b border-black/[0.06] bg-black/[0.02] px-4 py-2 text-[12px] text-cf-text-2">
              <span>本次会话已结束（记录只读）。</span>
              <button
                type="button"
                onClick={startNew}
                className="cf-btn secondary text-[12px]"
              >
                开始新会话
              </button>
            </div>
          )}

          <div className="cf-chat flex flex-1 flex-col gap-2.5 overflow-y-auto px-4 py-3">
            {loading && (
              <div className="text-[12px] text-cf-text-2">正在连接客服…</div>
            )}
            {messages.map((m) => (
              <div
                key={m.id}
                className={`cf-bubble ${m.role === "user" ? "user" : "bot"}`}
              >
                {m.role !== "user" && (
                  <div className="mb-0.5 text-[10px] font-medium text-cf-text-2">
                    {ROLE_LABEL[m.role] ?? m.role}
                  </div>
                )}
                {m.role === "user" ? (
                  <div className="cf-text">{m.content}</div>
                ) : (
                  <div className="cf-text text-[13px] [&_a]:text-cf-blue [&_a]:underline [&_p]:mb-1 [&_p]:last:mb-0">
                    <ReactMarkdown>{m.content}</ReactMarkdown>
                  </div>
                )}
              </div>
            ))}
            {sending && (
              <div className="cf-bubble bot text-[12px] text-cf-text-2">
                对方正在输入…
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {error && (
            <div className="border-t border-black/[0.06] px-4 py-1.5 text-[11px] text-cf-red">
              {error}
            </div>
          )}

          <footer className="border-t border-black/[0.06] p-3">
            <div className="flex items-end gap-2">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void onSend();
                  }
                }}
                rows={1}
                maxLength={4000}
                disabled={status === "closed" || sending}
                placeholder={
                  status === "closed"
                    ? "会话已结束"
                    : status === "escalated"
                      ? "人工客服处理中，可继续留言…"
                      : "输入您的问题，Enter 发送"
                }
                className="cf-input max-h-24 flex-1 resize-none text-[13px]"
              />
              <button
                type="button"
                onClick={onSend}
                disabled={!input.trim() || sending || status === "closed"}
                className="cf-btn primary"
              >
                发送
              </button>
            </div>
          </footer>
        </section>
      )}

      <button
        type="button"
        onClick={onToggle}
        className="fixed bottom-6 right-4 z-50 flex h-13 w-13 items-center justify-center rounded-full bg-cf-blue px-4 py-3 text-[12px] font-semibold text-white shadow-lg transition-transform hover:scale-105"
        aria-label="在线客服"
      >
        {open ? "收起" : "客服"}
      </button>
    </>
  );
}
