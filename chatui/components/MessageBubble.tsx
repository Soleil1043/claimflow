"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  AGENT_LABEL,
  briefInput,
  formatDuration,
  INTENT_LABEL,
  SOURCE_LABEL,
  type MessageSendResponse,
  type OcrResultResponse,
} from "@/lib/api";
import { MaterialCard } from "./MaterialCard";

/** 会话流条目：text 文本 / reply A06 富数据回复 / material 材料识别 / error 错误 / pending 打字中 */
export interface ChatEntry {
  id: number;
  role: "user" | "assistant";
  kind: "text" | "reply" | "material" | "error" | "pending";
  /** kind = text | error 的文本内容 */
  content?: string;
  /** kind = reply 的 A06 响应 */
  reply?: MessageSendResponse;
  /** kind = material 的 A07 响应 */
  ocr?: OcrResultResponse;
}

/** 合规三态展示映射（nodes/compliance.py verdict） */
const COMPLIANCE_VIEW: Record<string, { cls: string; label: string }> = {
  PASS: { cls: "ok", label: "合规通过" },
  MODIFY: { cls: "warn", label: "合规修订" },
  REJECT: { cls: "err", label: "合规拦截" },
};

function Toggle({
  label,
  open,
  onToggle,
}: {
  label: string;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button type="button" className="cf-toggle" onClick={onToggle} aria-expanded={open}>
      <span className={`chev ${open ? "open" : ""}`}>▸</span>
      {label}
    </button>
  );
}

/** A06 富数据区：意图/合规 pill + 转人工提示卡 + 处理过程/工具轨迹折叠明细 */
function ReplyMeta({ r }: { r: MessageSendResponse }) {
  const [openSteps, setOpenSteps] = useState(false);
  const [openTools, setOpenTools] = useState(false);
  const compliance = r.compliance_status ? COMPLIANCE_VIEW[r.compliance_status] : null;
  const steps = r.agent_steps ?? [];
  const tools = r.used_tools ?? [];
  const empty = !r.intent && !compliance && steps.length === 0 && tools.length === 0 && !r.need_human_intervention;
  if (empty) return null;

  return (
    <div className="cf-meta">
      <div className="flex flex-wrap items-center gap-1.5">
        {r.intent && (
          <span className="cf-pill info">意图 · {INTENT_LABEL[r.intent] ?? r.intent}</span>
        )}
        {compliance && <span className={`cf-pill ${compliance.cls}`}>{compliance.label}</span>}
      </div>

      {r.need_human_intervention && (
        <div className="rounded-[10px] border border-[rgba(255,149,0,0.35)] bg-[rgba(255,149,0,0.08)] px-2.5 py-1.5 text-[12px] text-[#8a5a00]">
          已转人工坐席处理{r.intervention_reason ? `：${r.intervention_reason}` : ""}
        </div>
      )}

      {steps.length > 0 && (
        <div>
          <Toggle
            label={`处理过程 · ${steps.length} 步`}
            open={openSteps}
            onToggle={() => setOpenSteps((v) => !v)}
          />
          <div className={`cf-collapse ${openSteps ? "open" : ""}`}>
            <div className="cf-collapse-inner">
              <div className="flex flex-col gap-1.5 pt-1.5">
                {steps.map((s) => (
                  <div key={s.step_index} className="rounded-[10px] bg-[rgba(0,0,0,0.03)] px-2.5 py-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="cf-pill muted">{AGENT_LABEL[s.agent] ?? s.agent}</span>
                      <span className="text-[11px] tabular-nums text-cf-text-2">
                        {formatDuration(s.duration_ms)}
                      </span>
                    </div>
                    {s.description && <div className="mt-1 text-[12px]">{s.description}</div>}
                    {s.summary && (
                      <div className="mt-0.5 text-[11px] leading-relaxed text-cf-text-2">{s.summary}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {tools.length > 0 && (
        <div>
          <Toggle
            label={`工具调用 · ${tools.length} 次`}
            open={openTools}
            onToggle={() => setOpenTools((v) => !v)}
          />
          <div className={`cf-collapse ${openTools ? "open" : ""}`}>
            <div className="cf-collapse-inner">
              <div className="mono-block flex flex-col gap-1 pt-1.5">
                {tools.map((t, i) => (
                  <div key={`${t.tool}-${i}`}>
                    <span className="font-semibold text-cf-blue-dark">{t.tool}</span>
                    <span className="text-cf-text-2"> {briefInput(t.input) || "{}"}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function MessageBubble({ entry }: { entry: ChatEntry }) {
  if (entry.kind === "material") {
    return entry.ocr ? <MaterialCard ocr={entry.ocr} /> : null;
  }

  if (entry.role === "user") {
    return (
      <div className="cf-bubble user">
        <div className="cf-text">{entry.content}</div>
      </div>
    );
  }

  if (entry.kind === "pending") {
    return (
      <div className="cf-bubble bot" aria-label="正在思考">
        <span className="cf-typing">
          <i />
          <i />
          <i />
        </span>
      </div>
    );
  }

  if (entry.kind === "error") {
    return (
      <div className="cf-bubble bot err">
        <div className="cf-text">{entry.content}</div>
      </div>
    );
  }

  return (
    <div className="cf-bubble bot">
      {/* LLM 回答为 markdown（Gradio Chatbot 亦默认渲染），用户/错误文本保持原样 */}
      <div className="cf-md">
        <ReactMarkdown>{entry.kind === "reply" ? (entry.reply?.answer ?? "") : (entry.content ?? "")}</ReactMarkdown>
      </div>
      {entry.kind === "reply" && entry.reply && <ReplyMeta r={entry.reply} />}
    </div>
  );
}
