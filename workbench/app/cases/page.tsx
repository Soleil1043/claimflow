import Link from "next/link";
import { CASE_STATUS_LABEL, KIND_LABEL, KIND_STYLE, formatTime, listCaseInterventions } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * 核赔工单列表（T087）：interrupt 挂起的案件（补件/复核签批/受理升级）。
 * 工单类型徽章 + 类型筛选（服务端 searchParams 过滤）。
 */
export default async function CaseQueuePage({
  searchParams,
}: {
  searchParams: Promise<{ kind?: string }>;
}) {
  const params = await searchParams;
  const kind = params.kind ?? "";

  let body: Awaited<ReturnType<typeof listCaseInterventions>> | null = null;
  let error = "";
  try {
    body = await listCaseInterventions();
  } catch (e) {
    error = String(e instanceof Error ? e.message : e);
  }

  const items = (body?.items ?? []).filter(
    (i) => kind === "" || i.human.kind === kind
  );

  const kinds = [
    { key: "", label: "全部" },
    { key: "supplement", label: "补件" },
    { key: "review", label: "核赔复核" },
    { key: "escape", label: "受理升级" },
  ];

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-[26px] font-bold leading-tight tracking-tight">核赔工单</h1>
          <p className="mt-1 text-[13px] text-cf-text-2">
            interrupt 挂起的核赔案件：补件 · 核赔复核签批 · 受理升级
          </p>
        </div>
        <div className="text-right text-[11px] text-cf-text-2">
          <div>共 {items.length} 条</div>
        </div>
      </header>

      <nav className="mb-5 inline-flex rounded-full bg-black/[0.05] p-1">
        {kinds.map((tab) => {
          const active = tab.key === kind;
          const href = tab.key === "" ? "/cases" : `/cases?kind=${tab.key}`;
          return (
            <Link
              key={tab.label}
              href={href}
              className={`rounded-full px-4 py-1.5 text-[13px] font-medium transition-colors duration-150 ${
                active
                  ? "bg-white text-cf-text shadow-sm"
                  : "text-cf-text-2 hover:text-cf-text"
              }`}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>

      {error && (
        <div className="cf-card border-cf-red/20 bg-cf-red/[0.06] px-4 py-3 text-[13px] text-[#B3261E]">
          后端不可达或返回错误:{error}
        </div>
      )}

      {body && items.length === 0 && (
        <div className="cf-card border-dashed py-16 text-center">
          <div className="text-[15px] font-medium text-cf-text">当前筛选下暂无工单</div>
          <div className="mt-1 text-[12px] text-cf-text-2">
            补件 / 核赔复核 / 受理升级的核赔案件会自动进入此队列
          </div>
        </div>
      )}

      {items.length > 0 && (
        <div className="cf-card overflow-hidden p-0">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-black/[0.06] bg-black/[0.02] text-left text-[11px] tracking-wide text-cf-text-2">
                <th className="px-4 py-2.5 font-medium">案件</th>
                <th className="px-4 py-2.5 font-medium">险种</th>
                <th className="px-4 py-2.5 font-medium">索赔金额</th>
                <th className="px-4 py-2.5 font-medium">工单类型</th>
                <th className="px-4 py-2.5 font-medium">原因 / 缺件</th>
                <th className="px-4 py-2.5 font-medium">状态</th>
                <th className="px-4 py-2.5 font-medium">时间</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={item.case_id}
                  className="border-b border-black/[0.04] transition-colors duration-100 last:border-0 hover:bg-black/[0.02]"
                >
                  <td className="px-4 py-3 font-mono text-[12px] text-cf-text-2">
                    {item.case_id}
                  </td>
                  <td className="px-4 py-3 text-cf-text">{item.case_type}</td>
                  <td className="px-4 py-3 tabular-nums text-cf-text">
                    {item.claimed_amount}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium ring-1 ${
                        KIND_STYLE[item.human.kind] ??
                        "bg-black/[0.04] text-cf-text-2 ring-black/[0.06]"
                      }`}
                    >
                      {KIND_LABEL[item.human.kind] ?? item.human.kind}
                    </span>
                  </td>
                  <td
                    className="max-w-[280px] truncate px-4 py-3 text-cf-text-2"
                    title={item.human.reason ?? ""}
                  >
                    {item.human.missing.length > 0
                      ? `缺件：${item.human.missing.join("、")}`
                      : item.human.reason ?? "-"}
                  </td>
                  <td className="px-4 py-3">
                    <span className="inline-flex items-center rounded-full bg-black/[0.04] px-2.5 py-0.5 text-[11px] text-cf-text-2">
                      {CASE_STATUS_LABEL[item.status] ?? item.status}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-[12px] tabular-nums text-cf-text-2">
                    {formatTime(item.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      href={`/cases/${item.case_id}`}
                      className="cf-btn secondary cf-pressable"
                    >
                      处理 →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
