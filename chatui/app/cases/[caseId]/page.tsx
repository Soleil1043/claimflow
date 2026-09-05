import Link from "next/link";
import AutoRefresh from "@/components/AutoRefresh";
import MaterialUpload from "@/components/MaterialUpload";
import { CONCLUSION_LABEL, formatTime, getCaseDetail, STATUS_LABEL } from "@/lib/case-api";
import { notFound } from "next/navigation";

export const dynamic = "force-dynamic";

const KIND_BADGE: Record<string, string> = {
  supplement: "bg-[#FF9500]/[0.12] text-[#8a5300]",
  review: "bg-cf-blue/[0.1] text-cf-blue",
  escape: "bg-cf-red/[0.1] text-[#B3261E]",
};

const KIND_LABEL: Record<string, string> = {
  supplement: "补件",
  review: "核赔复核",
  escape: "受理升级",
};

const EVENT_LABEL: Record<string, string> = {
  stage_result: "阶段结论",
  routing: "调度决策",
  guard_correction: "守卫纠错",
  human: "人工动作",
  material_upload: "材料上传",
  status_change: "状态流转",
};

export default async function CaseDetailPage({
  params,
}: {
  params: Promise<{ caseId: string }>;
}) {
  const { caseId } = await params;
  let detail;
  try {
    detail = await getCaseDetail(caseId);
  } catch {
    notFound();
  }
  if (!detail) notFound();

  const kind = detail.status === "supplement_pending" ? "supplement" : "review";
  const pending = detail.status === "supplement_pending" || detail.status === "referred";
  const doc = detail.decision_document;

  return (
    <main className="mx-auto max-w-3xl px-6 py-8 space-y-5">
      <Link href="/" className="text-[13px] text-cf-text-2 hover:text-cf-text">← 返回首页</Link>

      <AutoRefresh status={detail.status} jobStatus={detail.job?.status} />

      <header className="space-y-1">
        <h1 className="font-mono text-[22px] font-bold tracking-tight">{detail.case_id}</h1>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-cf-text-2">
          <span>状态：<b className="text-cf-text">{STATUS_LABEL[detail.status] ?? detail.status}</b></span>
          <span>险种：{detail.case_type}</span>
          <span>保单：{detail.policy_no}</span>
          <span>提交：{formatTime(detail.created_at)}</span>
        </div>
      </header>

      {/* 核定金额 */}
      {detail.approved_amount !== null && (
        <div className="cf-card px-5 py-4 text-center">
          <div className="cf-kicker mb-1">核定金额</div>
          <div className="text-[28px] font-bold tabular-nums text-cf-text">
            {detail.approved_amount} 元
          </div>
          {detail.final_decision && (
            <div className="mt-1 text-[13px] text-cf-text-2">
              {CONCLUSION_LABEL[detail.final_decision] ?? detail.final_decision}
            </div>
          )}
        </div>
      )}

      {/* 决定书 */}
      {doc && (
        <div className="cf-card px-5 py-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-[15px] font-semibold">{doc.title}</h2>
            <span className="rounded-full bg-black/[0.04] px-2 py-0.5 text-[11px] text-cf-text-2">
              v{doc.version} · {doc.issued_by}
            </span>
          </div>
          <pre className="whitespace-pre-wrap rounded-[10px] bg-black/[0.03] p-3 font-mono text-[12px] leading-relaxed text-cf-text">
            {doc.body}
          </pre>
        </div>
      )}

      {/* 补件提示 */}
      {detail.status === "supplement_pending" && (
        <div className="cf-card border-[#FF9500]/30 bg-[#FF9500]/[0.06] px-5 py-4">
          <div className="text-[14px] font-medium text-[#8a5300]">请补充材料</div>
          <p className="mt-1 text-[13px] text-cf-text-2">
            系统检测到您的申请材料不全，请上传缺失材料后系统将继续审核。
          </p>
          <div className="mt-3"><MaterialUpload caseId={detail.case_id} /></div>
        </div>
      )}

      {/* 转人工提示 */}
      {detail.status === "referred" && (
        <div className="cf-card border-cf-blue/20 bg-cf-blue/[0.06] px-5 py-4">
          <div className="text-[14px] font-medium text-cf-blue">已转人工处理</div>
          <p className="mt-1 text-[13px] text-cf-text-2">
            您的案件正在由理赔专员审核，请耐心等待。
          </p>
        </div>
      )}

      {/* 材料上传（非挂起态也可追加） */}
      {!pending && detail.status !== "referred" && detail.status !== "closed" && (
        <div className="cf-card px-5 py-4">
          <h2 className="cf-kicker mb-2">补充材料</h2>
          <MaterialUpload caseId={detail.case_id} />
        </div>
      )}

      {/* 时间线 */}
      <div className="cf-card px-5 py-4">
        <h2 className="cf-kicker mb-3">审核进度（{detail.timeline.length} 条）</h2>
        <ol className="space-y-2">
          {detail.timeline.map((e) => (
            <li key={e.seq} className="flex items-start gap-3 rounded-[10px] border border-black/[0.04] px-3 py-2">
              <span className="mt-0.5 rounded-full bg-black/[0.05] px-2 py-0.5 text-[11px] tabular-nums text-cf-text-2">
                {e.seq}
              </span>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                    KIND_BADGE[e.kind] ?? "bg-black/[0.04] text-cf-text-2"
                  }`}>
                    {EVENT_LABEL[e.kind] ?? e.kind}
                  </span>
                  {e.stage && <span className="font-mono text-[11px] text-cf-text-2">{e.stage}</span>}
                  <span className="ml-auto text-[10px] text-cf-text-2">{formatTime(e.created_at)}</span>
                </div>
                {e.payload && typeof e.payload === "object" && "targets" in (e.payload as Record<string, unknown>) && (
                  <div className="mt-0.5 text-[11px] text-cf-text-2">
                    派发 {((e.payload as Record<string, unknown>).targets as string[])?.join(" → ")}
                    {(e.payload as Record<string, unknown>).corrected ? "（守卫修正）" : ""}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
        {detail.timeline.length === 0 && (
          <div className="py-4 text-center text-[13px] text-cf-text-2">暂无进度</div>
        )}
      </div>
    </main>
  );
}
