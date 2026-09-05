/**
 * 后端 API 封装与类型定义（对齐 schemas/api.py）。
 *
 * 浏览器端走相对路径 /api/*，由 next.config.ts rewrites 代理到后端（同源无跨域）；
 * 服务端组件（RSC）的 fetch 不经过 rewrites，需要绝对地址（WORKBENCH_API_TARGET 可覆盖）。
 */
const API_BASE = process.env.WORKBENCH_API_TARGET ?? "http://localhost:8000";

/** JSON 请求封装（v2 核赔工单沿用；T104 自 v1 段保留） */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    ...init,
  });
  if (!resp.ok) {
    throw new Error(`API ${resp.status}: ${await resp.text().catch(() => "")}`);
  }
  return (await resp.json()) as T;
}

function apiUrl(path: string): string {
  return typeof window === "undefined" ? `${API_BASE}${path}` : path;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

export type CaseKind = "supplement" | "review" | "escape";

export interface CaseInterventionHuman {
  kind: string;
  reason: string | null;
  missing: string[];
}

export interface CaseInterventionItem {
  case_id: string;
  case_type: string;
  status: string;
  claimed_amount: string;
  human: CaseInterventionHuman;
  created_at: string;
}

export interface CaseInterventionListResponse {
  total: number;
  items: CaseInterventionItem[];
}

export interface CaseTimelineEvent {
  seq: number;
  kind: string;
  stage: string | null;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface CaseDecisionDocument {
  version: number;
  title: string;
  conclusion: string;
  approved_amount: string | null;
  issued_by: string;
  body: string;
}

export interface CaseJobInfo {
  job_id: number;
  action: string;
  status: string;
  outcome: string | null;
  attempt: number;
  max_attempts: number;
  error: string | null;
}

export interface CaseDetail {
  job?: CaseJobInfo | null;
  decision_issued?: boolean;
  case_id: string;
  user_id: string;
  policy_no: string;
  case_type: string;
  status: string;
  claimed_amount: string;
  approved_amount: string | null;
  final_decision: string | null;
  materials: { file_name: string; doc_type?: string; storage_path?: string }[];
  decision_document: CaseDecisionDocument | null;
  timeline: CaseTimelineEvent[];
  created_at: string;
  updated_at: string | null;
}

export interface CaseResolveResponse {
  case_id: string;
  status: string;
  final_decision: string | null;
  approved_amount: string | null;
  decision_document: CaseDecisionDocument | null;
}

export interface CaseResolveBody {
  action?: "confirm" | "rewrite";
  decision?: "approved" | "rejected" | "partial";
  approved_amount?: string;
  reason?: string;
  body?: string;
  note: string;
  resolved_by: string;
  added_materials?: { file_name: string; doc_type?: string }[];
}

export function listCaseInterventions(): Promise<CaseInterventionListResponse> {
  return request<CaseInterventionListResponse>("/api/v1/interventions/cases");
}

export function getCaseDetail(caseId: string): Promise<CaseDetail> {
  return request<CaseDetail>(`/api/v1/cases/${caseId}`);
}

export function resolveCaseIntervention(
  caseId: string,
  body: CaseResolveBody
): Promise<CaseResolveResponse> {
  return request<CaseResolveResponse>(`/api/v1/interventions/cases/${caseId}/resolve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** 材料上传（multipart，浏览器自动带 boundary；不经过 JSON request 封装） */
export async function uploadCaseMaterial(
  caseId: string,
  file: File,
  docType?: string
): Promise<{ case_status: string | null; materials_count: number }> {
  const form = new FormData();
  form.append("file", file);
  if (docType) form.append("doc_type", docType);
  const resp = await fetch(apiUrl(`/api/v1/cases/${caseId}/materials`), {
    method: "POST",
    body: form,
    cache: "no-store",
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${resp.statusText} ${detail}`.slice(0, 300));
  }
  return (await resp.json()) as { case_status: string | null; materials_count: number };
}

// ---------- 展示辅助 ----------

export const KIND_LABEL: Record<string, string> = {
  supplement: "补件",
  review: "核赔复核",
  escape: "受理升级",
};

export const KIND_STYLE: Record<string, string> = {
  supplement: "bg-[#FF9500]/[0.12] text-[#8a5300] ring-[#FF9500]/25",
  review: "bg-cf-blue/[0.1] text-cf-blue ring-cf-blue/25",
  escape: "bg-cf-red/[0.1] text-[#B3261E] ring-cf-red/25",
};

export const CASE_STATUS_LABEL: Record<string, string> = {
  received: "已受理",
  in_progress: "审核中",
  supplement_pending: "待补件",
  auto_issued: "已自动签发",
  referred: "已转人工",
  closed: "已关闭",
};
