"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { escalateTicket, resolveTicket } from "@/lib/api";

/**
 * 坐席处理动作（仅 pending 工单显示）：
 * - 解决并回写结论：触发后端 interrupt 恢复（T037），结论经合规复审后返回用户
 * - 升级转出：线下渠道处理，不恢复会话
 */
export default function ResolveForm({ ticketId }: { ticketId: number }) {
  const router = useRouter();
  const [note, setNote] = useState("");
  const [agent, setAgent] = useState("");
  const [busy, setBusy] = useState<"resolve" | "escalate" | null>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ answer: string; resumed: boolean } | null>(null);

  const canSubmit = note.trim().length > 0 && agent.trim().length > 0 && busy === null;

  async function onResolve() {
    setBusy("resolve");
    setError("");
    setResult(null);
    try {
      const resp = await resolveTicket(ticketId, {
        resolution_note: note.trim(),
        resolved_by: agent.trim(),
      });
      setResult({ answer: resp.answer, resumed: resp.resumed });
      router.refresh(); // 刷新工单状态与会话轨迹（坐席回复已落审计）
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }

  async function onEscalate() {
    setBusy("escalate");
    setError("");
    try {
      await escalateTicket(ticketId, { resolved_by: agent.trim() || "unknown-agent" });
      router.refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-[1fr_200px]">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-slate-500">
            处理结论（经合规复审后返回用户）
          </span>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            maxLength={4000}
            placeholder="例：经人工核实，该情况不符合理赔条件，已向您电话解释说明。"
            className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
          />
        </label>
        <div className="space-y-3">
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-500">坐席标识</span>
            <input
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
              maxLength={64}
              placeholder="agent-01"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
            />
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={!canSubmit}
              onClick={onResolve}
              className="flex-1 rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {busy === "resolve" ? "处理中…" : "解决并回写结论"}
            </button>
          </div>
          <button
            type="button"
            disabled={busy !== null}
            onClick={onEscalate}
            className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy === "escalate" ? "转出中…" : "升级转出（线下处理）"}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-200">
          操作失败：{error}
        </div>
      )}
      {result && (
        <div className="space-y-1.5 rounded-md bg-emerald-50 px-3 py-2.5 text-sm ring-1 ring-emerald-200">
          <div className="font-medium text-emerald-800">
            ✓ 工单已解决{result.resumed ? "（会话已恢复，以下为返回用户的回答）" : "（图无挂起，结论已落审计）"}
          </div>
          <div className="whitespace-pre-wrap text-slate-700">{result.answer}</div>
        </div>
      )}
    </div>
  );
}
