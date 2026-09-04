import CaseForm from "@/components/CaseForm";

export const dynamic = "force-dynamic";

/** 核赔案件提交门户（T091）：理赔申请表单 + 材料清单 → 提交后跳转案件详情。 */
export default function Home() {
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
      <CaseForm />
    </main>
  );
}
