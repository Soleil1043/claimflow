import { NextResponse, type NextRequest } from "next/server";

/** 与 lib/api.ts STAFF_COOKIE 同名（middleware 跑 edge runtime，不引 lib 避免拉入 next/headers）。 */
const STAFF_COOKIE = "cf_staff_key";

/**
 * 坐席 Key 注入（T215/I12，D092）：浏览器发往同源 /api/* 的请求，从 HttpOnly cookie
 * 取 Key 写入 X-Staff-Key 后再经 rewrites 代理到后端。浏览器 JS 永远拿不到 Key。
 *
 * 客户端自带的 X-Staff-Key 一律剥除——只信 cookie（防页面脚本伪造头绕过登录态）。
 * /api/staff-session 是本工作台自己的登录接口，不转发后端。
 */
export function middleware(req: NextRequest) {
  const headers = new Headers(req.headers);
  headers.delete("x-staff-key");
  const key = req.cookies.get(STAFF_COOKIE)?.value;
  if (key) headers.set("X-Staff-Key", key);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  matcher: ["/api/v1/:path*"],
};
