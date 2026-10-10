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

/** SSE 流事件（T180）：token 流式片段 / done 终局 / error LLM 故障。 */
export type SupportStreamEvent =
  | { type: "token"; content: string }
  | { type: "done"; status: SupportStatus }
  | { type: "error"; detail: string };

/**
 * 流式发消息（T180，D085）：POST + fetch ReadableStream 解析 SSE 帧
 * （非 EventSource-GET：复用 POST 语义、内容不进 URL/访问日志）。
 * onToken 逐段回调；resolve 值为 done 事件的终局 status；error 事件抛错
 * （此时用户消息已在服务端落库，调用方刷新时间线即可恢复）。
 */
export async function streamSupportMessage(
  id: string,
  content: string,
  onToken: (chunk: string) => void
): Promise<SupportStatus> {
  const resp = await fetch(
    apiUrl(`/api/v1/support/conversations/${id}/messages/stream`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
      cache: "no-store",
    }
  );
  if (!resp.ok || !resp.body) {
    const detail = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${resp.statusText} ${detail}`.slice(0, 300));
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalStatus: SupportStatus = "ai";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const evt = JSON.parse(line.slice(6)) as SupportStreamEvent;
      if (evt.type === "token") {
        if (evt.content) onToken(evt.content);
      } else if (evt.type === "done") {
        finalStatus = evt.status;
      } else {
        throw new Error(evt.detail || "在线客服暂时不可用，请稍后重试");
      }
    }
  }
  return finalStatus;
}
