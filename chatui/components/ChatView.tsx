"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, createConversation, sendMessage, uploadMaterial } from "@/lib/api";
import { MessageBubble, type ChatEntry } from "./MessageBubble";

const WELCOME =
  "您好，我是保险理赔智能助手。您可以问我：\n" +
  "- 保单查询（如「查一下保单 POL-2025-0001」）\n" +
  "- 赔付金额估算（如「保单 POL-2025-0001 住院花了15800元能赔多少？」）\n" +
  "- 理赔规则咨询（如「阑尾炎手术有等待期吗」）";

/** 示例问题（与 Gradio 演示界面同集，点击直接发送） */
const EXAMPLES = [
  "保单 POL-2025-0001 住院花了15800元能赔多少？",
  "查一下保单 POL-2025-0002 的保障范围",
  "阑尾炎手术有等待期吗",
  "理赔需要准备什么材料",
];

const ACCEPT = ".png,.jpg,.jpeg,.webp,.bmp,.pdf,.docx";

let nextId = 1;
function makeEntry(partial: Omit<ChatEntry, "id">): ChatEntry {
  return { id: nextId++, ...partial };
}

/** 错误文案：网络不可达提示启动后端；HTTP 错误带状态与 detail */
function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.status === undefined
      ? `⚠️ ${err.message}，请确认后端已启动（uv run uvicorn app.main:app --port 8000）。`
      : `⚠️ 后端处理失败：${err.message}`;
  }
  return `⚠️ 未知错误：${String(err)}`;
}

/**
 * 对话主视图（T063）：会话惰性创建（A02）→ 发消息（A06）/传材料（A07），
 * 回复以富数据结构化展示（意图/合规/处理过程/工具轨迹）。
 */
export function ChatView() {
  const [entries, setEntries] = useState<ChatEntry[]>(() => [
    makeEntry({ role: "assistant", kind: "text", content: WELCOME }),
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const conversationRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // 新消息 / 打字态变化时滚到底部（尊重 prefers-reduced-motion）
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, [entries, busy]);

  function finishPending(entry: Omit<ChatEntry, "id">) {
    setEntries((prev) => {
      const idx = prev.map((e) => e.kind).lastIndexOf("pending");
      if (idx === -1) return [...prev, makeEntry(entry)];
      const next = [...prev];
      next[idx] = { id: next[idx].id, ...entry };
      return next;
    });
  }

  async function ensureConversation(): Promise<string> {
    if (!conversationRef.current) {
      conversationRef.current = await createConversation();
    }
    return conversationRef.current;
  }

  function autogrow() {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }

  async function submitText(raw: string) {
    const content = raw.trim();
    if (!content || busy) return;
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setEntries((prev) => [
      ...prev,
      makeEntry({ role: "user", kind: "text", content }),
      makeEntry({ role: "assistant", kind: "pending" }),
    ]);
    setBusy(true);
    try {
      const conversationId = await ensureConversation();
      const reply = await sendMessage(conversationId, content);
      finishPending({ role: "assistant", kind: "reply", reply });
    } catch (err) {
      finishPending({ role: "assistant", kind: "error", content: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  async function submitFile(file: File) {
    if (busy) return;
    setEntries((prev) => [
      ...prev,
      makeEntry({ role: "user", kind: "text", content: `📎 已上传材料：${file.name}` }),
      makeEntry({ role: "assistant", kind: "pending" }),
    ]);
    setBusy(true);
    try {
      const conversationId = await ensureConversation();
      const ocr = await uploadMaterial(conversationId, file);
      finishPending({ role: "assistant", kind: "material", ocr });
    } catch (err) {
      finishPending({ role: "assistant", kind: "error", content: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  function resetConversation() {
    if (busy) return;
    conversationRef.current = null;
    setEntries([makeEntry({ role: "assistant", kind: "text", content: WELCOME })]);
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4">
        <div className="cf-msglist mx-auto w-full max-w-3xl">
          {entries.map((e) => (
            <MessageBubble key={e.id} entry={e} />
          ))}
        </div>
      </div>

      <div className="cf-composer cf-rise px-4 pb-4 pt-2">
        <div className="mx-auto w-full max-w-3xl">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {EXAMPLES.map((q) => (
              <button
                key={q}
                type="button"
                className="cf-chip"
                disabled={busy}
                onClick={() => void submitText(q)}
              >
                {q}
              </button>
            ))}
            <button
              type="button"
              className="cf-btn secondary ml-auto shrink-0"
              disabled={busy}
              onClick={resetConversation}
            >
              🔄 新会话
            </button>
          </div>

          <div className="flex items-end gap-2">
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPT}
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void submitFile(f);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              className="cf-btn secondary shrink-0"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
            >
              📎 上传材料
            </button>
            <textarea
              ref={textareaRef}
              className="cf-textarea flex-1"
              rows={1}
              placeholder="输入您的问题，如：保单 POL-2025-0001 住院花了15800元能赔多少？"
              value={input}
              disabled={busy}
              onChange={(e) => {
                setInput(e.target.value);
                autogrow();
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void submitText(input);
                }
              }}
            />
            <button
              type="button"
              className="cf-btn primary shrink-0"
              disabled={busy || !input.trim()}
              onClick={() => void submitText(input)}
            >
              发送
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
