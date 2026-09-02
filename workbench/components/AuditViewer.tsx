"use client";

import { useState } from "react";
import type { AgentStepItem, ToolTraceItem } from "@/lib/api";

function JsonBlock({ data }: { data: unknown }) {
  return (
    <div className="mono-block rounded-[10px] bg-[#1D1D1F] p-2.5 text-[#E8E8ED]">
      {JSON.stringify(data, null, 2)}
    </div>
  );
}

function TraceSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] font-semibold tracking-wide text-cf-text-2">{title}</div>
      {children}
    </div>
  );
}

/**
 * assistant 消息的审计展开区（T057 / D030）：
 * 展开/收起用 cf-collapse（grid-rows 过渡，250ms 标准缓动，reduced-motion 自动降级）。
 */
export default function AuditViewer({
  toolTrace,
  agentSteps,
}: {
  toolTrace: ToolTraceItem[] | null;
  agentSteps: AgentStepItem[] | null;
}) {
  const [open, setOpen] = useState(false);
  const hasContent = (toolTrace?.length ?? 0) + (agentSteps?.length ?? 0) > 0;
  if (!hasContent) return null;

  return (
    <div className="mt-2 border-t border-black/[0.06] pt-2">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="cf-pressable text-[12px] font-medium text-cf-blue transition-colors hover:text-cf-blue-dark"
      >
        {open ? "▾ 收起执行审计" : "▸ 展开执行审计（工具调用 / Agent 步骤）"}
      </button>
      <div className={`cf-collapse ${open ? "open" : ""}`}>
        <div className="cf-collapse-inner">
          <div className="mt-2 space-y-4">
            {agentSteps && agentSteps.length > 0 && (
              <TraceSection title={`AGENT 步骤（${agentSteps.length}）`}>
                <div className="space-y-1.5">
                  {agentSteps.map((step) => (
                    <div
                      key={step.step_index}
                      className="rounded-[10px] border border-black/[0.06] bg-white px-3 py-2 text-[12px] shadow-sm"
                    >
                      <div className="flex items-center gap-2">
                        <span className="cf-pill info font-mono">{step.agent}</span>
                        <span
                          className={
                            step.status === "done"
                              ? "font-medium text-cf-green"
                              : "font-medium text-cf-red"
                          }
                        >
                          {step.status === "done" ? "✓ 完成" : `✗ ${step.status}`}
                        </span>
                        <span className="tabular-nums text-cf-text-2">{step.duration_ms} ms</span>
                      </div>
                      <div className="mt-1 text-cf-text-2">
                        {step.description}
                        {step.summary ? ` — ${step.summary}` : ""}
                      </div>
                    </div>
                  ))}
                </div>
              </TraceSection>
            )}
            {toolTrace && toolTrace.length > 0 && (
              <TraceSection title={`工具调用（${toolTrace.length}）`}>
                <div className="space-y-2">
                  {toolTrace.map((trace, i) => (
                    <div
                      key={i}
                      className="rounded-[10px] border border-black/[0.06] bg-white px-3 py-2 shadow-sm"
                    >
                      <div className="mb-1.5 font-mono text-[12px] font-semibold text-cf-text">
                        🔧 {trace.tool}
                      </div>
                      <div className="grid gap-2 md:grid-cols-2">
                        <div>
                          <div className="mb-1 text-[11px] text-cf-text-2">入参</div>
                          <JsonBlock data={trace.input} />
                        </div>
                        <div>
                          <div className="mb-1 text-[11px] text-cf-text-2">出参</div>
                          <JsonBlock data={trace.output} />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </TraceSection>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
