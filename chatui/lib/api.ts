/**
 * 后端 API 封装与类型定义（对齐 schemas/api.py，A02 / A06 / A07）。
 *
 * 浏览器端走相对路径 /api/*（含 /health），由 next.config.ts rewrites 代理到后端（同源无跨域）；
 * 与 workbench/lib/api.ts 同模式，后端地址 CHATUI_API_TARGET 可覆盖。
 */
const API_BASE = process.env.CHATUI_API_TARGET ?? "http://localhost:8000";

function apiUrl(path: string): string {
  return typeof window === "undefined" ? `${API_BASE}${path}` : path;
}

/** 工具轨迹项（A06 used_tools，业务工具白名单过滤后） */
export interface ToolTraceItem {
  tool: string;
  input: Record<string, unknown>;
  output?: Record<string, unknown>;
}

/** 执行步骤项（task_plan + shared_data 派生，T047 口径） */
export interface AgentStepItem {
  step_index: number;
  agent: string;
  description: string;
  status: string;
  duration_ms: number;
  summary: string;
}

/** 合规三态（nodes/compliance.py verdict 枚举） */
export type ComplianceVerdict = "PASS" | "MODIFY" | "REJECT";

/** A06 发消息响应（MessageSendResponse） */
export interface MessageSendResponse {
  answer: string;
  intent: string | null;
  used_tools: ToolTraceItem[];
  agent_steps: AgentStepItem[] | null;
  compliance_status: ComplianceVerdict | null;
  need_human_intervention: boolean;
  intervention_reason: string | null;
}

/** A07 材料识别响应（OcrResultResponse） */
export interface OcrResultResponse {
  patient_name: string | null;
  diagnosis: string | null;
  amount: number | null;
  date: string | null;
  /** vision（图片识别） / text_model（PDF/Word 文本提取） / mock_fallback（失败降级） */
  source: string;
  filename: string;
  file_type: string;
}

/** 带状态码的错误：网络不可用 status 为 undefined，界面据此区分提示文案 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(apiUrl(path), {
      ...init,
      cache: "no-store",
    });
  } catch {
    throw new ApiError("无法连接后端服务");
  }
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new ApiError(`${resp.status} ${detail}`.slice(0, 300), resp.status);
  }
  return (await resp.json()) as T;
}

/** A02 创建会话，返回 conversation_id */
export async function createConversation(userId = "chatui-demo"): Promise<string> {
  const r = await request<{ conversation_id: string }>("/api/v1/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  return r.conversation_id;
}

/** A06 发消息（触发完整主图流程） */
export function sendMessage(conversationId: string, content: string): Promise<MessageSendResponse> {
  return request<MessageSendResponse>(`/api/v1/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

/** A07 上传材料（图片/PDF/Word，触发字段提取） */
export function uploadMaterial(conversationId: string, file: File): Promise<OcrResultResponse> {
  const form = new FormData();
  form.append("file", file);
  return request<OcrResultResponse>(`/api/v1/conversations/${conversationId}/materials`, {
    method: "POST",
    body: form,
  });
}

/** 后端健康探测（头部状态 pill） */
export async function checkHealth(): Promise<boolean> {
  try {
    const resp = await fetch(apiUrl("/health"), { cache: "no-store" });
    return resp.ok;
  } catch {
    return false;
  }
}

// ---------- 展示辅助 ----------

/** 意图五分类中文标签（schemas/agent.py IntentType） */
export const INTENT_LABEL: Record<string, string> = {
  simple_faq: "简单咨询",
  single_domain: "单域办理",
  complex_consult: "复杂咨询",
  chitchat: "闲聊",
  other: "其他",
};

/** Worker Agent 中文名（agents/） */
export const AGENT_LABEL: Record<string, string> = {
  orchestrator: "调度",
  claim: "理赔核算",
  medical: "医疗审核",
  compliance: "合规风控",
};

/** A06 来源标记文案（对齐 ui/app.py upload_material） */
export const SOURCE_LABEL: Record<string, string> = {
  vision: "真实识别（vision）",
  text_model: "文本提取（PDF/Word）",
  mock_fallback: "Mock 兜底数据",
};

/** 工具入参摘要（截断长值，对齐 ui/app.py _brief） */
export function briefInput(data: Record<string, unknown>): string {
  return Object.entries(data)
    .map(([k, v]) => {
      const s = String(v);
      return `${k}=${s.slice(0, 40)}${s.length > 40 ? "…" : ""}`;
    })
    .join(", ");
}

/** 耗时格式化：ms < 1000 显示毫秒，否则秒 */
export function formatDuration(ms: number): string {
  if (!ms) return "-";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** 金额格式化：千分位 + ¥，空值显示「未识别」 */
export function formatAmount(amount: number | null): string {
  if (amount === null || amount === undefined) return "未识别";
  return `¥${amount.toLocaleString("zh-CN")}`;
}
