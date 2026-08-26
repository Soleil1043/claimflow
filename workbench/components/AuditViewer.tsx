"use client";

import { useState } from "react";
import type { AgentStepItem, ToolTraceItem } from "@/lib/api";

function JsonBlock({ data }: { data: unknown }) {
  return (
    <div className="mono-block rounded bg-slate-800 p-2.5 text-slate-100">
      {JSON.stringify(data, null, 2)}
    </div>
  );
}

function TraceSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-semibold text-slate-500">{title}</div>
      {children}
    </div>
  );
}

/**
 * assistant 消息的审计展开区：工具调用入参/出参与 Agent 步骤档案。
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
    <div className="mt-2 border-t border-slate-200 pt-2">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="text-xs font-medium text-sky-700 hover:text-sky-900"
      >
        {open ? "▾ 收起执行审计" : "▸ 展开执行审计（工具调用 / Agent 步骤）"}
      </button>
      {open && (
        <div className="mt-2 space-y-4">
          {agentSteps && agentSteps.length > 0 && (
            <TraceSection title={`Agent 步骤（${agentSteps.length}）`}>
              <div className="space-y-1.5">
                {agentSteps.map((step) => (
                  <div
                    key={step.step_index}
                    className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs"
                  >
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-indigo-100 px-1.5 py-0.5 font-mono text-indigo-800">
                        {step.agent}
                      </span>
                      <span
                        className={
                          step.status === "done" ? "text-emerald-700" : "text-red-600 font-medium"
                        }
                      >
                        {step.status === "done" ? "✓ 完成" : `✗ ${step.status}`}
                      </span>
                      <span className="text-slate-400">{step.duration_ms} ms</span>
                    </div>
                    <div className="mt-1 text-slate-600">
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
                    className="rounded-md border border-slate-200 bg-white px-3 py-2"
                  >
                    <div className="mb-1.5 font-mono text-xs font-semibold text-slate-700">
                      🔧 {trace.tool}
                    </div>
                    <div className="grid gap-2 md:grid-cols-2">
                      <div>
                        <div className="mb-1 text-[11px] text-slate-400">入参</div>
                        <JsonBlock data={trace.input} />
                      </div>
                      <div>
                        <div className="mb-1 text-[11px] text-slate-400">出参</div>
                        <JsonBlock data={trace.output} />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </TraceSection>
          )}
        </div>
      )}
    </div>
  );
}
