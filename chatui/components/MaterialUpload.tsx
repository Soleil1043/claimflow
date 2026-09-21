"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { fetchMaterialCatalog, uploadCaseMaterial, type MaterialCatalogLine } from "@/lib/case-api";

export default function MaterialUpload({ caseId, disabled }: { caseId: string; disabled?: boolean }) {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [docType, setDocType] = useState("");
  // 材料类型目录（险种分组，后端 pack 单源；T126 动态化）
  const [catalog, setCatalog] = useState<MaterialCatalogLine[]>([]);

  useEffect(() => {
    fetchMaterialCatalog().then((lines) => {
      setCatalog(lines);
      setDocType((cur) => cur || lines[0]?.docs[0]?.value || "");
    });
  }, []);
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
          {catalog.map((g) => (
            <optgroup key={g.line} label={g.label}>
              {g.docs.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
            </optgroup>
          ))}
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
