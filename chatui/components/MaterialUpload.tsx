"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { uploadCaseMaterial } from "@/lib/case-api";

const DOC_TYPES = [
  { value: "invoice", label: "医疗发票" },
  { value: "diagnosis", label: "诊断证明" },
  { value: "cost_list", label: "费用清单" },
  { value: "medical_record", label: "病历" },
];

export default function MaterialUpload({ caseId, disabled }: { caseId: string; disabled?: boolean }) {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [docType, setDocType] = useState("cost_list");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  async function onUpload() {
    const file = fileRef.current?.files?.[0];
    if (!file || busy) return;
    setBusy(true); setError(""); setMsg("");
    try {
      const resp = await uploadCaseMaterial(caseId, file, docType);
      setMsg(`已上传（共 ${resp.materials_count} 份），流程状态：${resp.case_status ?? "已更新"}`);
      if (fileRef.current) fileRef.current.value = "";
      router.refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-2 md:grid-cols-[1fr_140px_auto]">
        <input ref={fileRef} type="file" disabled={disabled}
          accept=".jpg,.jpeg,.png,.webp,.bmp,.pdf,.docx" className="cf-input" />
        <select value={docType} onChange={(e) => setDocType(e.target.value)} disabled={disabled} className="cf-input">
          {DOC_TYPES.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
        </select>
        <button type="button" disabled={busy || disabled} onClick={onUpload} className="cf-btn primary">
          {busy ? "上传中…" : "上传"}
        </button>
      </div>
      <p className="text-[11px] text-cf-text-2">
        支持图片 / PDF / Word；补件挂起的案件上传后自动恢复核赔流程。
      </p>
      {error && <div className="rounded-[8px] bg-cf-red/[0.08] px-3 py-2 text-[12px] text-[#B3261E]">{error}</div>}
      {msg && <div className="rounded-[8px] bg-cf-green/[0.08] px-3 py-2 text-[12px] text-[#1F7A38]">✓ {msg}</div>}
    </div>
  );
}
