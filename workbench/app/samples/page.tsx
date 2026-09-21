import NarrativeSampleTable from "@/components/NarrativeSampleTable";
import { listNarrativeSamples } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * 决定书叙述抽评页（T139，缺口#4）：LLM 叙述质量的人工量化口径——
 * 自动签发案件按确定性比例采样，坐席 pass/revise 评审落审计，
 * 通过率对齐设计文档口径（≥97%）。
 */
export default async function NarrativeSamplesPage() {
  let data: Awaited<ReturnType<typeof listNarrativeSamples>> | null = null;
  let error = "";
  try {
    data = await listNarrativeSamples();
  } catch (e) {
    error = String(e instanceof Error ? e.message : e);
  }

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <header className="mb-6">
        <h1 className="text-[26px] font-bold leading-tight tracking-tight">叙述抽评</h1>
        <p className="mt-1 text-[13px] text-cf-text-2">
          自动签发案件决定书叙述的人工抽评队列：通过 / 需改进 评审落审计
        </p>
      </header>

      {error && (
        <div className="cf-card border-cf-red/20 bg-cf-red/[0.06] px-4 py-3 text-[13px] text-[#B3261E]">
          后端不可达或返回错误:{error}
        </div>
      )}

      {data && <NarrativeSampleTable data={data} />}
    </main>
  );
}
