"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { resolveCaseIntervention, type CaseResolveBody } from "@/lib/api";

/**
 * 核赔工单处理表单（T087，kind-aware）：
 * - review：confirm（签发既有结论）/ rewrite（改判：结论 + 坐席金额 + 意见 + 可选重写正文）
 * - escape：登记受理升级意见（转专家线下）
 * 坐席文本由后端红线复审（违规不签发，安全兜底 referred）。
 */
export default function CaseResolveForm({
  caseId,
  kind,
}: {
  caseId: string;
  kind: string;
}) {
  const router = useRouter();
  const [action, setAction] = useState<"confirm" | "rewrite">("confirm");
  const [decision, setDecision] = useState<"approved" | "rejected" | "partial">("approved");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [body, setBody] = useState("");
  const [note, setNote] = useState("");
  const [agent, setAgent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  const canSubmit =
    agent.trim().length > 0 &&
    note.trim().length > 0 &&
    (kind !== "review" || action !== "rewrite" || decision === "approved" || amount.trim().length > 0) &&
    !busy;

  async function onSubmit() {
    setBusy(true);
    setError("");
    setDone(false);
    const payload: CaseResolveBody = {
      note: note.trim(),
      resolved_by: agent.trim(),
    };
    if (kind === "review") {
      payload.action = action;
      if (action === "rewrite") {
        payload.decision = decision;
        if (amount.trim()) payload.approved_amount = amount.trim();
        if (reason.trim()) payload.reason = reason.trim();
        if (body.trim()) payload.body = body.trim();
      }
    }
    try {
      await resolveCaseIntervention(caseId, payload);
      setDone(true);
      router.refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {kind === "review" && (
        <div className="inline-flex rounded-full bg-black/[0.05] p-1">
          {(
            [
              { key: "confirm", label: "签批确认" },
              { key: "rewrite", label: "改判 / 重写" },
            ] as const
          ).map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => setAction(tab.key)}
              className={`rounded-full px-4 py-1.5 text-[13px] font-medium transition-colors duration-150 ${
                action === tab.key
                  ? "bg-white text-cf-text shadow-sm"
                  : "text-cf-text-2 hover:text-cf-text"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      )}

      {kind === "review" && action === "rewrite" && (
        <div className="grid gap-3 md:grid-cols-[160px_200px_1fr]">
          <label className="block">
            <span className="mb-1 block text-[12px] font-medium text-cf-text-2">改判结论</span>
            <select
              value={decision}
              onChange={(e) => setDecision(e.target.value as typeof decision)}
              className="cf-input"
            >
              <option value="approved">核准赔付</option>
              <option value="partial">部分赔付</option>
              <option value="rejected">拒绝赔付</option>
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-[12px] font-medium text-cf-text-2">
              坐席核定金额（元）
            </span>
            <input
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
              placeholder="如 4640.00"
              className="cf-input"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-[12px] font-medium text-cf-text-2">改判理由</span>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
              placeholder="如：部分自费材料扣除"
              className="cf-input"
            />
          </label>
        </div>
      )}

      {kind === "review" && action === "rewrite" && (
        <label className="block">
          <span className="mb-1 block text-[12px] font-medium text-cf-text-2">
            重写决定书正文（可选；留空则由系统按改判结论重新渲染）
          </span>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={4}
            maxLength={4000}
            className="cf-input font-mono text-[12px]"
          />
        </label>
      )}

      {kind === "escape" && (
        <div className="rounded-[10px] bg-black/[0.03] px-3 py-2 text-[12px] text-cf-text-2">
          受理升级案件由专家线下处理；登记处理意见后案件归档为转人工终态。
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-[1fr_220px]">
        <label className="block">
          <span className="mb-1 block text-[12px] font-medium text-cf-text-2">
            {kind === "review" ? "复核意见" : "处理意见"}（过合规红线复审后落审计）
          </span>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            maxLength={1000}
            placeholder="例：金额超自动签发线，人工复核材料齐全、责任明确，同意签发。"
            className="cf-input"
          />
        </label>
        <div className="space-y-3">
          <label className="block">
            <span className="mb-1 block text-[12px] font-medium text-cf-text-2">坐席标识</span>
            <input
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
              maxLength={64}
              placeholder="agent-01"
              className="cf-input"
            />
          </label>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={onSubmit}
            className="cf-btn primary w-full"
          >
            {busy ? "提交中…" : "提交处理"}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-[10px] bg-cf-red/[0.08] px-3 py-2 text-[13px] text-[#B3261E] ring-1 ring-cf-red/15">
          操作失败:{error}
        </div>
      )}
      {done && (
        <div className="rounded-[10px] bg-cf-green/[0.08] px-3 py-2.5 text-[13px] text-[#1F7A38] ring-1 ring-cf-green/20">
          ✓ 已提交，核赔流程已恢复（案件状态见上方概要，页面数据已刷新）
        </div>
      )}
    </div>
  );
}
