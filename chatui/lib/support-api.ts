/**
 * 门户在线客服 API 封装（T136，对齐 app/api/v1/support.py）。
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

export type SupportRole = "user" | "assistant" | "agent";
export type SupportStatus = "ai" | "escalated" | "closed";

export interface SupportMessage {
  id: number;
  role: SupportRole;
  content: string;
  created_at: string;
}

export interface SupportConversation {
  conversation_id: string;
  status: SupportStatus;
  escalated_reason: string | null;
  created_at: string;
  escalated_at: string | null;
  closed_at: string | null;
}

export function createSupportConversation(): Promise<{
  conversation_id: string;
  status: string;
  created_at: string;
}> {
  return request("/api/v1/support/conversations", { method: "POST" });
}

export function getSupportConversation(id: string): Promise<SupportConversation> {
  return request(`/api/v1/support/conversations/${id}`);
}

export function listSupportMessages(
  id: string
): Promise<{ total: number; items: SupportMessage[] }> {
  return request(`/api/v1/support/conversations/${id}/messages`);
}

export function sendSupportMessage(
  id: string,
  content: string
): Promise<{ status: SupportStatus; reply: string | null }> {
  return request(`/api/v1/support/conversations/${id}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}
