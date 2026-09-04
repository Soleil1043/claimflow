import type { CaseTimelineEvent } from "@/lib/api";

/**
 * 案件时间线（T087）：case_events 按 seq 回放——阶段结论 / orchestrator 路由决策
 * （含守卫修正标记）/ 状态流转 / 材料上传。纯服务端渲染。
 */

const KIND_LABEL: Record<string, string> = {
  stage_result: "阶段结论",
  routing: "调度决策",
  guard_correction: "守卫纠错",
  human: "人工动作",
  material_upload: "材料上传",
  status_change: "状态流转",
};

const KIND_STYLE: Record<string, string> = {
  routing: "bg-cf-blue/[0.1] text-cf-blue ring-cf-blue/25",
  guard_correction: "bg-cf-red/[0.1] text-[#B3261E] ring-cf-red/25",
  status_change: "bg-[#FF9500]/[0.12] text-[#8a5300] ring-[#FF9500]/25",
};

function summarize(event: CaseTimelineEvent): string {
  const p = event.payload;
  if (event.kind === "routing" && p) {
    const targets = Array.isArray(p.targets) ? (p.targets as string[]).join(" → ") : "";
    const corrected = p.corrected ? " · 守卫已修正" : "";
    const mode = p.mode ? ` · ${String(p.mode)}` : "";
    return `派发 ${targets || "-"}${corrected}${mode}`;
  }
  if (event.kind === "stage_result" && p) {
    if (p.verdict) return `verdict=${String(p.verdict)}  置信度=${String(p.confidence ?? "-")}`;
    if (p.completeness)
      return `完整性=${String(p.completeness)}  置信度=${String(p.confidence ?? "-")}`;
    if (p.approved_amount !== undefined)
      return `核定金额=${String(p.approved_amount ?? "-")}`;
  }
  if (event.kind === "status_change" && p) {
    return `状态 → ${String(p.status ?? "-")}${p.reason ? `（${String(p.reason)}）` : ""}`;
  }
  if (event.kind === "material_upload" && p) {
    return `上传 ${String(p.file_name ?? "")}`;
  }
  return "";
}

export default function CaseTimeline({
  events,
}: {
  events: CaseTimelineEvent[];
}) {
  if (events.length === 0) {
    return <div className="py-6 text-center text-[13px] text-cf-text-2">暂无审计事件</div>;
  }
  return (
    <ol className="space-y-2.5">
      {events.map((event) => {
        const style = KIND_STYLE[event.kind] ?? "bg-black/[0.04] text-cf-text-2 ring-black/[0.06]";
        const summary = summarize(event);
        return (
          <li
            key={event.seq}
            className="flex items-start gap-3 rounded-[10px] border border-black/[0.04] px-3 py-2.5"
          >
            <span className="mt-0.5 inline-flex min-w-[28px] justify-center rounded-full bg-black/[0.05] px-2 py-0.5 text-[11px] tabular-nums text-cf-text-2">
              {event.seq}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${style}`}
                >
                  {KIND_LABEL[event.kind] ?? event.kind}
                </span>
                {event.stage && (
                  <span className="font-mono text-[11px] text-cf-text-2">{event.stage}</span>
                )}
                <span className="ml-auto text-[11px] tabular-nums text-cf-text-2">
                  {new Date(event.created_at).toLocaleString("zh-CN", { hour12: false })}
                </span>
              </div>
              {summary && (
                <div className="mt-1 truncate text-[12px] text-cf-text-2" title={summary}>
                  {summary}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
