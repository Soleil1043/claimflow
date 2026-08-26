/**
 * 后端 API 封装与类型定义（对齐 schemas/api.py）。
 *
 * 浏览器端走相对路径 /api/*，由 next.config.ts rewrites 代理到后端（同源无跨域）；
 * 服务端组件（RSC）的 fetch 不经过 rewrites，需要绝对地址（WORKBENCH_API_TARGET 可覆盖）。
 */
const API_BASE = process.env.WORKBENCH_API_TARGET ?? "http://localhost:8000";

function apiUrl(path: string): string {
  return typeof window === "undefined" ? `${API_BASE}${path}` : path;
}

export type TicketStatus = "pending" | "resolved" | "transferred_out";

export interface TicketSummary {
  id: number;
  conversation_id: string;
  user_id: string;
  status: TicketStatus;
  intervention_reason: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface TicketListResponse {
  total: number;
  items: TicketSummary[];
}

export interface ToolTraceItem {
  tool: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
}

export interface AgentStepItem {
  step_index: number;
  agent: string;
  description: string;
  status: string;
  duration_ms: number;
  summary: string;
}

export interface MessageItem {
  id: number;
  role: "user" | "assistant";
  content: string;
  intent: string | null;
  tool_trace: ToolTraceItem[] | null;
  agent_steps: AgentStepItem[] | null;
  compliance_status: string | null;
  created_at: string;
}

export interface Violation {
  type: string;
  detail: string;
  suggestion?: string | null;
}

export interface ComplianceSnapshot {
  verdict?: string;
  violations?: Violation[];
  risk_score?: number;
  reason?: string;
}

export interface TicketDetail extends TicketSummary {
  compliance_snapshot: ComplianceSnapshot | null;
  resolution_note: string | null;
  resolved_by: string | null;
  conversation: {
    id: string;
    user_id: string;
    status: string;
    created_at: string;
  };
  messages: MessageItem[];
}

export interface ResolveResponse {
  ticket: TicketSummary;
  /** 坐席结论经图内合规复审后的最终回答（T037 interrupt 恢复） */
  answer: string;
  resumed: boolean;
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

export function listTickets(status?: TicketStatus): Promise<TicketListResponse> {
  const qs = status ? `?status=${status}` : "";
  return request<TicketListResponse>(`/api/v1/interventions${qs}`);
}

export function getTicket(id: number | string): Promise<TicketDetail> {
  return request<TicketDetail>(`/api/v1/interventions/${id}`);
}

export function resolveTicket(
  id: number | string,
  body: { resolution_note: string; resolved_by: string }
): Promise<ResolveResponse> {
  return request<ResolveResponse>(`/api/v1/interventions/${id}/resolve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function escalateTicket(
  id: number | string,
  body: { resolved_by: string; note?: string | null }
): Promise<TicketSummary> {
  return request<TicketSummary>(`/api/v1/interventions/${id}/escalate`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ---------- 展示辅助 ----------

export const STATUS_LABEL: Record<TicketStatus, string> = {
  pending: "待处理",
  resolved: "已解决",
  transferred_out: "已转出",
};

export const STATUS_TABS: { key: TicketStatus | ""; label: string }[] = [
  { key: "", label: "全部" },
  { key: "pending", label: "待处理" },
  { key: "resolved", label: "已解决" },
  { key: "transferred_out", label: "已转出" },
];

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}
