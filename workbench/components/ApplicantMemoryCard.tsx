"use client";

import { useState } from "react";
import {
  deleteApplicantMemory,
  type ApplicantMemory,
} from "@/lib/api";

/**
 * 申请人核赔档案卡（T129 读闭环 + T138 治理）：坐席视角展示跨案件档案，
 * 每条可删除（CaseEvent 审计留痕；rebuild 脚本重跑会重建，非 tombstone）。
 */
export default function ApplicantMemoryCard({
  userId,
  memories,
}: {
  userId: string;
  memories: ApplicantMemory[];
}) {
  const [items, setItems] = useState(memories);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function onDelete(caseId: string) {
    setBusyId(caseId);
    setError("");
    try {
      await deleteApplicantMemory(userId, caseId, "workbench");
      setItems((prev) => prev.filter((m) => m.case_id !== caseId));
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusyId(null);
    }
  }

  if (items.length === 0) return null;

  return (
    <section className="cf-card px-5 py-4">
      <h2 className="cf-kicker mb-3">申请人核赔档案（近 {items.length} 案）</h2>
      <div className="space-y-2">
        {items.map((m) => (
          <div
            key={m.case_id}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[10px] bg-black/[0.03] px-3 py-2 text-[12px]"
          >
            <span className="font-mono text-cf-text-2">{m.case_id}</span>
            <span className="rounded-full bg-black/[0.05] px-2 py-0.5">{m.case_type}</span>
            <span className="text-cf-text-2">
              {m.outcome === "auto_issued" ? "自动签发" : m.outcome === "closed" ? "坐席办结" : "转人工"}
              {m.final_decision ? ` · ${m.final_decision}` : ""}
            </span>
            {m.approved_amount && (
              <span className="tabular-nums text-cf-text">{m.approved_amount} 元</span>
            )}
            {m.incident_date && <span className="text-cf-text-2">出险 {m.incident_date}</span>}
            {typeof m.confidence === "number" && (
              <span
                className={`tabular-nums ${
                  m.confidence >= 0.8 ? "text-cf-text-2" : "text-[#8a5300]"
                }`}
                title="写入时终态链路置信度"
              >
                置信 {m.confidence.toFixed(2)}
              </span>
            )}
            {m.reason && (
              <span className="w-full truncate text-cf-text-2" title={m.reason}>
                {m.reason}
              </span>
            )}
            <button
              type="button"
              onClick={() => onDelete(m.case_id)}
              disabled={busyId === m.case_id}
              className="ml-auto rounded-full px-2 py-0.5 text-[11px] text-cf-red/80 transition-colors hover:bg-cf-red/[0.08] disabled:opacity-40"
              title="删除该档案条目（审计留痕；rebuild 脚本重跑会重建）"
            >
              {busyId === m.case_id ? "删除中…" : "删除"}
            </button>
          </div>
        ))}
      </div>
      {error && (
        <div className="mt-2 rounded-[8px] bg-cf-red/[0.08] px-3 py-1.5 text-[11px] text-[#B3261E]">
          删除失败:{error}
        </div>
      )}
    </section>
  );
}
