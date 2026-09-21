import CaseForm from "@/components/CaseForm";

export const dynamic = "force-dynamic";

/** 客服预填参数（claim_draft_link 工具生成，D057-4：预填跳转不代替提交）。 */
export interface CaseFormInitialValues {
  caseType?: string;
  amount?: string;
  date?: string;
  description?: string;
}

/**
 * 核赔案件提交门户（T091）：理赔申请表单 + 材料清单 → 提交后跳转案件详情。
 * T136：客服对话收集的信息经 query 参数预填表单（case_type/incident_date/
 * claimed_amount/description），客户核对补全后自行提交。
 */
export default async function Home({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const pick = (key: string): string | undefined => {
    const v = params[key];
    return typeof v === "string" && v.trim() ? v.trim() : undefined;
  };
  const initialValues: CaseFormInitialValues = {
    caseType: pick("case_type"),
    amount: pick("claimed_amount"),
    date: pick("incident_date"),
    description: pick("description"),
  };
  const prefill = Boolean(
    initialValues.caseType ||
      initialValues.amount ||
      initialValues.date ||
      initialValues.description
  );

  return (
    <main className="mx-auto max-w-2xl px-6 py-8">
      <header className="mb-5 text-center">
        <h1 className="text-[24px] font-bold tracking-tight text-cf-text">
          智能核赔平台
        </h1>
        <p className="mt-1 text-[13px] text-cf-text-2">
          提交理赔申请与材料，系统自动审核并出具理赔决定书
        </p>
      </header>

      {prefill && (
        <div className="mb-4 rounded-[10px] bg-cf-blue/[0.08] px-4 py-2.5 text-[12px] text-cf-text ring-1 ring-cf-blue/15">
          已为您预填在线客服收集的信息（
          {[
            initialValues.caseType && "险种",
            initialValues.date && "出险日期",
            initialValues.amount && "金额",
            initialValues.description && "出险经过",
          ]
            .filter(Boolean)
            .join("、")}
          ），请核对补全后提交。
        </div>
      )}

      <CaseForm initialValues={prefill ? initialValues : undefined} />
    </main>
  );
}
