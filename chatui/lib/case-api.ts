/**
 * 核赔案件 API 封装（T091，对齐 schemas/api.py B01-B03 + interventions）。
 *
 * 浏览器端走相对路径 /api/*，由 next.config.ts rewrites 代理到后端；
 * 服务端组件 fetch 不经过 rewrites，需绝对地址（CHATUI_API_TARGET 可覆盖）。
 */
const API_BASE = process.env.CHATUI_API_TARGET ?? "http://localhost:8000";

function apiUrl(path: string): string {
  return typeof window === "undefined" ? `${API_BASE}${path}` : path;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(apiUrl(path), {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${resp.statusText} ${detail}`.slice(0, 300));
  }
  return (await resp.json()) as T;
}

// ---------- 案件提交 ----------

export interface CaseMaterialRef {
  file_name: string;
  doc_type?: string;
}

export interface CaseCreateRequest {
  user_id: string;
  policy_no: string;
  claimed_amount: string;
  incident_date: string;
  incident_description: string;
  declared_case_type?: string;
  materials: CaseMaterialRef[];
}

export interface CaseDecisionDocument {
  version: number;
  title: string;
  conclusion: string;
  approved_amount: string | null;
  issued_by: string;
  body: string;
}

export interface CaseHumanInfo {
  kind: string;
  reason: string | null;
  missing: string[];
}

export interface CaseSubmitResponse {
  case_id: string;
  case_type: string;
  status: string;
  final_decision: string | null;
  approved_amount: string | null;
  decision_document: CaseDecisionDocument | null;
  human: CaseHumanInfo | null;
  idempotent: boolean;
}

export function submitCase(body: CaseCreateRequest): Promise<CaseSubmitResponse> {
  return request<CaseSubmitResponse>("/api/v1/cases", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ---------- 案件详情 ----------

export interface CaseTimelineEvent {
  seq: number;
  kind: string;
  stage: string | null;
  payload: Record<string, unknown> | null;
  created_at: string;
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
  human?: { kind: string; reason?: string | null; missing?: string[] } | null;
  case_id: string;
  user_id: string;
  policy_no: string;
  case_type: string;
  status: string;
  claimed_amount: string;
  approved_amount: string | null;
  final_decision: string | null;
  materials: Record<string, unknown>[];
  decision_document: CaseDecisionDocument | null;
  timeline: CaseTimelineEvent[];
  created_at: string;
  updated_at: string | null;
}

export function getCaseDetail(caseId: string): Promise<CaseDetail> {
  return request<CaseDetail>(`/api/v1/cases/${caseId}`);
}

// ---------- 材料上传 ----------

export async function uploadCaseMaterial(
  caseId: string,
  file: File,
  docType?: string,
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

export const STATUS_LABEL: Record<string, string> = {
  received: "已受理",
  in_progress: "审核中",
  supplement_pending: "待补件",
  // 客户视图不区分签发方（workbench 的"已自动签发"是坐席视图的有意差异，勿统一）
  auto_issued: "已签发",
  referred: "已转人工",
  closed: "已关闭",
};

export const CONCLUSION_LABEL: Record<string, string> = {
  approved: "核准赔付",
  rejected: "拒绝赔付",
  partial: "部分赔付",
  referred: "转人工处理",
};

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

/**
 * 案件是否仍在交付中（T109 单源口径）：job 在飞（queued/running）或受理未终态。
 * 挂起态（supplement_pending/referred）本身不算 active——resume 在飞由 job 覆盖。
 */
export function isCaseActive(
  status: string,
  jobStatus?: string | null
): boolean {
  return (
    jobStatus === "queued" ||
    jobStatus === "running" ||
    status === "received" ||
    status === "in_progress"
  );
}
