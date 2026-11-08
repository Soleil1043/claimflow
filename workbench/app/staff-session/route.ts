import { NextResponse, type NextRequest } from "next/server";
import { STAFF_COOKIE } from "@/lib/api";

/**
 * 坐席会话（T215/I12，D092）：POST 登录（校验 Key 后下发 HttpOnly cookie）/ DELETE 退出。
 *
 * 放在 /staff-session 而非 /api/* 下——next.config rewrites 会把 /api/* 整体转发后端。
 * 登录前用后端一个坐席端点校验 Key（401 即无效），无效 Key 不落 cookie。
 */
const API_BASE = process.env.WORKBENCH_API_TARGET ?? "http://localhost:8000";
const MAX_AGE_S = 8 * 60 * 60; // 一个班次

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => ({}))) as { key?: unknown };
  const key = typeof body.key === "string" ? body.key.trim() : "";
  if (!key) {
    return NextResponse.json({ detail: "请输入坐席 Key" }, { status: 422 });
  }
  const probe = await fetch(`${API_BASE}/api/v1/interventions/jobs/dead`, {
    headers: { "X-Staff-Key": key },
    cache: "no-store",
  }).catch(() => null);
  if (probe === null) {
    return NextResponse.json({ detail: "后端不可达，请稍后重试" }, { status: 502 });
  }
  if (probe.status === 401) {
    return NextResponse.json({ detail: "坐席 Key 无效" }, { status: 401 });
  }
  const resp = NextResponse.json({ ok: true });
  resp.cookies.set(STAFF_COOKIE, key, {
    httpOnly: true,
    sameSite: "strict",
    secure: req.nextUrl.protocol === "https:",
    path: "/",
    maxAge: MAX_AGE_S,
  });
  return resp;
}

export async function DELETE() {
  const resp = NextResponse.json({ ok: true });
  resp.cookies.delete(STAFF_COOKIE);
  return resp;
}
