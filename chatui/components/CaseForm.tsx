"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { submitCase, type CaseSubmitResponse } from "@/lib/case-api";

const DOC_TYPES = [
  { value: "invoice", label: "医疗发票" },
  { value: "diagnosis", label: "诊断证明" },
  { value: "cost_list", label: "费用清单" },
  { value: "medical_record", label: "病历" },
];

export default function CaseForm() {
  const router = useRouter();
  const [userId, setUserId] = useState("");
  const [policyNo, setPolicyNo] = useState("");
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState<{ name: string; docType: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<CaseSubmitResponse | null>(null);

  function addFile() {
    setFiles([...files, { name: `材料${files.length + 1}.pdf`, docType: "invoice" }]);
  }

  function removeFile(idx: number) {
    setFiles(files.filter((_, i) => i !== idx));
  }

  function updateFileType(idx: number, docType: string) {
    const next = [...files];
    next[idx] = { ...next[idx], docType };
    setFiles(next);
  }

  const canSubmit =
    userId.trim() && policyNo.trim() && amount.trim() && date && description.trim() && !busy;

  async function onSubmit() {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const resp = await submitCase({
        user_id: userId.trim(),
        policy_no: policyNo.trim(),
        claimed_amount: amount.trim(),
        incident_date: date,
        incident_description: description.trim(),
        materials: files.map((f) => ({ file_name: f.name, doc_type: f.docType })),
      });
      setResult(resp);
      if (resp.case_id) {
        setTimeout(() => router.push(`/cases/${resp.case_id}`), 1500);
      }
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="cf-card px-6 py-5 space-y-4">
      <h2 className="text-[17px] font-semibold text-cf-text">提交理赔申请</h2>

      <div className="grid gap-4 md:grid-cols-2">
        <label className="block">
          <span className="mb-1 block text-[12px] font-medium text-cf-text-2">您的标识</span>
          <input value={userId} onChange={(e) => setUserId(e.target.value)}
            maxLength={64} placeholder="如 zhangwei" className="cf-input" />
        </label>
        <label className="block">
          <span className="mb-1 block text-[12px] font-medium text-cf-text-2">保单号</span>
          <input value={policyNo} onChange={(e) => setPolicyNo(e.target.value)}
            maxLength={32} placeholder="如 POL-2025-0001" className="cf-input" />
        </label>
        <label className="block">
          <span className="mb-1 block text-[12px] font-medium text-cf-text-2">索赔金额（元）</span>
          <input value={amount} onChange={(e) => setAmount(e.target.value)}
            inputMode="decimal" placeholder="如 15800.00" className="cf-input" />
        </label>
        <label className="block">
          <span className="mb-1 block text-[12px] font-medium text-cf-text-2">出险日期</span>
          <input value={date} onChange={(e) => setDate(e.target.value)}
            type="date" className="cf-input" />
        </label>
      </div>

      <label className="block">
        <span className="mb-1 block text-[12px] font-medium text-cf-text-2">出险描述</span>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)}
          rows={3} maxLength={2000}
          placeholder="描述出险经过、诊断、费用构成等"
          className="cf-input" />
      </label>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[12px] font-medium text-cf-text-2">
            材料清单（{files.length} 份）
          </span>
          <button type="button" onClick={addFile}
            className="text-[12px] text-cf-blue hover:underline">+ 添加材料</button>
        </div>
        {files.length === 0 && (
          <div className="rounded-[8px] border border-dashed border-black/[0.1] px-3 py-2 text-[12px] text-cf-text-2">
            暂无材料，点击上方添加
          </div>
        )}
        <div className="space-y-2">
          {files.map((f, i) => (
            <div key={i} className="flex items-center gap-2">
              <input value={f.name} readOnly className="cf-input flex-1 text-[12px]" />
              <select value={f.docType} onChange={(e) => updateFileType(i, e.target.value)}
                className="cf-input w-[140px] text-[12px]">
                {DOC_TYPES.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
              <button type="button" onClick={() => removeFile(i)}
                className="cf-btn secondary text-[12px]">删除</button>
            </div>
          ))}
        </div>
      </div>

      <button type="button" disabled={!canSubmit} onClick={onSubmit}
        className="cf-btn primary w-full">
        {busy ? "提交中…" : "提交理赔申请"}
      </button>

      {error && (
        <div className="rounded-[10px] bg-cf-red/[0.08] px-3 py-2 text-[13px] text-[#B3261E] ring-1 ring-cf-red/15">
          提交失败:{error}
        </div>
      )}
      {result && (
        <div className="rounded-[10px] bg-cf-green/[0.08] px-3 py-3 text-[13px] ring-1 ring-cf-green/20 space-y-1">
          <div className="font-medium text-[#1F7A38]">
            ✓ 案件 {result.case_id} {result.idempotent ? "（重复提交，返回既有结论）" : "已受理"}
          </div>
          {result.final_decision && (
            <div>结论：{result.final_decision} · 核定 {result.approved_amount} 元</div>
          )}
          {result.human && (
            <div className="text-cf-orange">需人工处理：{result.human.reason}</div>
          )}
          <div className="text-cf-text-2">正在跳转到案件详情…</div>
        </div>
      )}
    </div>
  );
}
