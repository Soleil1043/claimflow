"use client";

import { SOURCE_LABEL, formatAmount, type OcrResultResponse } from "@/lib/api";

/** A07 材料识别结果卡：结构化字段 + 来源标记（对齐 ui/app.py upload_material 展示口径） */
export function MaterialCard({ ocr }: { ocr: OcrResultResponse }) {
  const rows: [string, string][] = [
    ["患者姓名", ocr.patient_name || "未识别"],
    ["诊断", ocr.diagnosis || "未识别"],
    ["金额", formatAmount(ocr.amount)],
    ["日期", ocr.date || "未识别"],
  ];
  return (
    <div className="cf-card cf-rise self-start" role="status" style={{ width: "min(100%, 26rem)" }}>
      <div className="flex items-center justify-between gap-2 px-4 py-2.5" style={{ borderBottom: "1px solid var(--cf-hairline)" }}>
        <span className="text-[13px] font-semibold">📋 材料识别结果</span>
        <span className={`cf-pill ${ocr.source === "mock_fallback" ? "muted" : "info"}`}>
          {SOURCE_LABEL[ocr.source] ?? ocr.source}
        </span>
      </div>
      <dl className="flex flex-col px-4 py-2">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-3 py-1 text-[13px]">
            <dt className="shrink-0 text-cf-text-2">{k}</dt>
            <dd className="text-right font-medium tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="px-4 py-1.5 text-[11px] text-cf-text-2" style={{ borderTop: "1px solid var(--cf-hairline)" }}>
        {ocr.filename} · {ocr.file_type}
      </div>
    </div>
  );
}
