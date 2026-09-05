import Link from "next/link";
import CaseResolveForm from "@/components/CaseResolveForm";
import CaseTimeline from "@/components/CaseTimeline";
import MaterialUploadForm from "@/components/MaterialUploadForm";
import {
  CASE_STATUS_LABEL,
  KIND_LABEL,
  KIND_STYLE,
  formatTime,
  getCaseDetail,
} from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * 案件详情（T087）：概要 + 决定书 + 审计时间线（含 orchestrator 路由决策与
 * 守卫修正）+ kind-aware 处理表单（复核签批 / 补件上传 / 升级登记）。
 */
export default async function CaseDetailPage({
  params,
}: {
  params: Promise<{ caseId: string }>;
}) {
  const { caseId } = await params;

  let detail: Awaited<ReturnType<typeof getCaseDetail>> | null = null;
  let error = "";
  try {
    detail = await getCaseDetail(caseId);
  } catch (e) {
    error = String(e instanceof Error ? e.message : e);
  }

  if (error || !detail) {
    return (
      <main className="mx-auto max-w-4xl px-6 py-12">
        <div className="cf-card border-cf-red/20 bg-cf-red/[0.06] px-4 py-4 text-[13px] text-[#B3261E]">
          案件加载失败:{error}
        </div>
        <Link href="/cases" className="cf-btn secondary mt-4 cf-pressable">
          ← 返回核赔工单
        </Link>
      </main>
    );
  }

  // 挂起 kind 单源 = 交付回执（D047）；escape 案此前会被状态猜测误标为 review
  const kind = detail.human?.kind ?? (detail.status === "supplement_pending" ? "supplement" : "review");
  const pending = detail.status === "supplement_pending" || detail.status === "referred";

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-5 flex items-center justify-between">
        <Link href="/cases" className="text-[13px] text-cf-text-2 hover:text-cf-text">
          ← 返回核赔工单
        </Link>
        <span
          className={`inline-flex items-center rounded-full px-3 py-1 text-[12px] font-medium ring-1 ${
            KIND_STYLE[kind] ?? "bg-black/[0.04] text-cf-text-2 ring-black/[0.06]"
          }`}
        >
          {KIND_LABEL[kind] ?? kind}
        </span>
      </div>

      <header className="mb-5">
        <h1 className="font-mono text-[22px] font-bold tracking-tight">{detail.case_id}</h1>
        <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-cf-text-2">
          <span>
            状态：
            <span className="font-medium text-cf-text">
              {CASE_STATUS_LABEL[detail.status] ?? detail.status}
            </span>
          </span>
          <span>险种：{detail.case_type}</span>
          <span>保单：{detail.policy_no}</span>
          <span>索赔：{detail.claimed_amount} 元</span>
          <span>提交：{formatTime(detail.created_at)}</span>
        </div>
      </header>

      <div className="grid gap-5">
        {/* 概要 */}
        <section className="cf-card px-5 py-4">
          <h2 className="cf-kicker mb-3">案件概要</h2>
          <div className="grid gap-x-6 gap-y-2 text-[13px] md:grid-cols-2">
            <div>
              核定金额：
              <span className="font-semibold tabular-nums text-cf-text">
                {detail.approved_amount ?? "-"}
              </span>{" "}
              元
            </div>
            <div>
              最终结论：
              <span className="font-medium text-cf-text">{detail.final_decision ?? "-"}</span>
            </div>
            <div className="md:col-span-2">
              已收材料：
              {detail.materials.length > 0 ? (
                <span className="text-cf-text-2">
                  {detail.materials.map((m) => m.file_name).join("、")}
                </span>
              ) : (
                <span className="text-cf-text-2">无</span>
              )}
            </div>
          </div>
        </section>

        {/* 决定书 */}
        {detail.decision_document && (
          <section className="cf-card px-5 py-4">
            <h2 className="cf-kicker mb-3">
              决定书（v{detail.decision_document.version} ·{" "}
              {detail.decision_document.issued_by}
              {!detail.decision_issued && (
                <span className="ml-1 rounded bg-[#FF9500]/[0.15] px-1.5 py-0.5 text-[10px] text-[#8a5300]">
                  草稿 · 未签发
                </span>
              )}）
            </h2>
            <div className="mb-2 flex flex-wrap items-center gap-2 text-[12px] text-cf-text-2">
              <span>{detail.decision_document.title}</span>
              <span className="rounded-full bg-black/[0.04] px-2 py-0.5">
                {detail.decision_document.conclusion}
              </span>
              <span className="tabular-nums">
                {detail.decision_document.approved_amount ?? "-"} 元
              </span>
            </div>
            <pre className="whitespace-pre-wrap rounded-[10px] bg-black/[0.03] p-3 font-mono text-[12px] leading-relaxed text-cf-text">
              {detail.decision_document.body}
            </pre>
          </section>
        )}

        {/* 处理表单（仅挂起中） */}
        {pending ? (
          <section className="cf-card px-5 py-4">
            <h2 className="cf-kicker mb-3">坐席处理</h2>
            <div className="space-y-5">
              <CaseResolveForm caseId={detail.case_id} kind={kind} />
              {kind === "supplement" && <MaterialUploadForm caseId={detail.case_id} />}
            </div>
          </section>
        ) : (
          <section className="cf-card px-5 py-4 text-[13px] text-cf-text-2">
            案件已办结（{CASE_STATUS_LABEL[detail.status] ?? detail.status}），无需处理。
          </section>
        )}

        {/* 审计时间线 */}
        <section className="cf-card px-5 py-4">
          <h2 className="cf-kicker mb-3">审计时间线（{detail.timeline.length} 条）</h2>
          <CaseTimeline events={detail.timeline} />
        </section>
      </div>
    </main>
  );
}
