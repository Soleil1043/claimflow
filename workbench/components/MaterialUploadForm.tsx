"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { fetchMaterialCatalog, uploadCaseMaterial, type MaterialCatalogLine } from "@/lib/api";

/**
 * 补件材料上传（T087）：multipart 上传 → 后端自动恢复补件挂起的核赔流程
 * （material_review 重跑 → 回 orchestrator），上传响应携带恢复后的案件状态。
 */
export default function MaterialUploadForm({ caseId }: { caseId: string }) {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [docType, setDocType] = useState("");
  // 材料类型目录（险种分组，后端 pack 单源；T126 动态化——四险种补件都可声明）
  const [catalog, setCatalog] = useState<MaterialCatalogLine[]>([]);

  useEffect(() => {
    fetchMaterialCatalog().then((lines) => {
      setCatalog(lines);
      setDocType((cur) => cur || lines[0]?.docs[0]?.value || "");
    });
  }, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<string | null>(null);

  async function onUpload() {
    const file = fileRef.current?.files?.[0];
    if (!file || busy) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const resp = await uploadCaseMaterial(caseId, file, docType);
      setResult(
        `已上传 ${resp.materials_count} 份材料，流程状态：${resp.case_status ?? "已更新"}`
      );
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
      <div className="grid gap-3 md:grid-cols-[1fr_160px_auto]">
        <input ref={fileRef} type="file" accept=".jpg,.jpeg,.png,.webp,.bmp,.pdf,.docx" className="cf-input" />
        <select value={docType} onChange={(e) => setDocType(e.target.value)} className="cf-input">
          {catalog.map((g) => (
            <optgroup key={g.line} label={g.label}>
              {g.docs.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
            </optgroup>
          ))}
        </select>
        <button
          type="button"
          disabled={busy}
          onClick={onUpload}
          className="cf-btn primary"
        >
          {busy ? "上传中…" : "上传材料"}
        </button>
      </div>
      <p className="text-[11px] text-cf-text-2">
        支持图片 / PDF / Word；补件挂起的案件上传缺失材料后自动恢复核赔流程。
      </p>
      {error && (
        <div className="rounded-[10px] bg-cf-red/[0.08] px-3 py-2 text-[13px] text-[#B3261E] ring-1 ring-cf-red/15">
          上传失败:{error}
        </div>
      )}
      {result && (
        <div className="rounded-[10px] bg-cf-green/[0.08] px-3 py-2 text-[13px] text-[#1F7A38] ring-1 ring-cf-green/20">
          ✓ {result}
        </div>
      )}
    </div>
  );
}
