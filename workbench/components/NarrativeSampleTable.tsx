"use client";

import { useState } from "react";
import {
  formatTime,
  reviewNarrative,
  type NarrativeSampleListResponse,
} from "@/lib/api";

/**
 * 叙述抽评表（T139，缺口#4）：待评审样本行内评审（pass/revise + 评语），
 * 顶部共享坐席标识；评审后 router.refresh 由父页触发（此处自行移除行）。
 */
export default function NarrativeSampleTable({
  data,
}: {
  data: NarrativeSampleListResponse;
}) {
  const [items, setItems] = useState(data.items);
  const [agent, setAgent] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<"pass" | "revise">("pass");
  const [comment, setComment] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [doneCount, setDoneCount] = useState(0);

  const agentOk = agent.trim().length > 0;

  async function onSubmit(caseId: string) {
    setBusyId(caseId);
    setError("");
    try {
      await reviewNarrative(caseId, {
        agent: agent.trim(),
        verdict,
        comment: comment.trim(),
      });
      setItems((prev) => prev.filter((i) => i.case_id !== caseId));
      setOpenId(null);
      setComment("");
      setDoneCount((n) => n + 1);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-[12px] text-cf-text-2">
          <span>坐席标识</span>
          <input
            value={agent}
            onChange={(e) => setAgent(e.target.value)}
            maxLength={64}
            placeholder="agent-01"
            className="cf-input w-[160px]"
          />
        </div>
        <div className="text-[12px] tabular-nums text-cf-text-2">
          采样 {data.stats.sampled} · 已评 {data.stats.reviewed}（通过{" "}
          {data.stats.passed} / 需改进 {data.stats.revised}）
          {data.stats.reviewed > 0 && (
            <span className="ml-1 font-medium text-cf-text">
              通过率 {((data.stats.passed / data.stats.reviewed) * 100).toFixed(1)}%
            </span>
          )}
          {doneCount > 0 && <span className="ml-1 text-cf-green">· 本次已评 {doneCount}</span>}
        </div>
      </div>

      {error && (
        <div className="rounded-[10px] bg-cf-red/[0.08] px-3 py-2 text-[13px] text-[#B3261E] ring-1 ring-cf-red/15">
          评审失败:{error}
        </div>
      )}

      {items.length === 0 ? (
        <div className="cf-card border-dashed py-14 text-center">
          <div className="text-[14px] font-medium text-cf-text">当前无待评审叙述样本</div>
          <div className="mt-1 text-[12px] text-cf-text-2">
            自动签发案件按抽样比例进入评审队列（manual_review_sample_rate）
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <div key={item.case_id} className="cf-card px-5 py-4">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
                <span className="font-mono text-[12px] text-cf-text-2">{item.case_id}</span>
                <span className="rounded-full bg-black/[0.05] px-2 py-0.5 text-[11px]">
                  {item.case_type}
                </span>
                <span className="text-cf-text-2">{item.final_decision ?? "-"}</span>
                {item.approved_amount && (
                  <span className="tabular-nums">{item.approved_amount} 元</span>
                )}
                <span className="text-[11px] text-cf-text-2">
                  采样于 {formatTime(item.sampled_at)}
                </span>
                <button
                  type="button"
                  onClick={() => setOpenId(openId === item.case_id ? null : item.case_id)}
                  className="cf-btn secondary cf-pressable ml-auto"
                >
                  {openId === item.case_id ? "收起" : "评审叙述 →"}
                </button>
              </div>

              {openId === item.case_id && (
                <div className="mt-3 space-y-3 border-t border-black/[0.06] pt-3">
                  <details>
                    <summary className="cursor-pointer text-[12px] text-cf-text-2">
                      决定书全文（叙述段=核定依据）
                    </summary>
                    <pre className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-[10px] bg-black/[0.03] px-3 py-2 text-[12px] leading-relaxed">
                      {item.narrative ?? "（无决定书）"}
                    </pre>
                  </details>
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="inline-flex rounded-full bg-black/[0.05] p-1">
                      {(
                        [
                          { key: "pass", label: "通过" },
                          { key: "revise", label: "需改进" },
                        ] as const
                      ).map((tab) => (
                        <button
                          key={tab.key}
                          type="button"
                          onClick={() => setVerdict(tab.key)}
                          className={`rounded-full px-4 py-1.5 text-[13px] font-medium transition-colors ${
                            verdict === tab.key
                              ? "bg-white text-cf-text shadow-sm"
                              : "text-cf-text-2 hover:text-cf-text"
                          }`}
                        >
                          {tab.label}
                        </button>
                      ))}
                    </div>
                    <button
                      type="button"
                      disabled={!agentOk || busyId === item.case_id}
                      onClick={() => onSubmit(item.case_id)}
                      className="cf-btn primary"
                    >
                      {busyId === item.case_id ? "提交中…" : "提交评审"}
                    </button>
                  </div>
                  <textarea
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    rows={2}
                    maxLength={1000}
                    placeholder="评审评语（可选）：叙述是否准确引用条款、有无承诺性表述…"
                    className="cf-input text-[13px]"
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
