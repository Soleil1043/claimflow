import type { NextConfig } from "next";

/**
 * dev/start 代理：浏览器 /api/*（含 /health）直连本地后端 8000（A02/A06/A07），
 * 同源无跨域；后端地址可用 CHATUI_API_TARGET 覆盖（与 workbench 同模式）。
 */
const nextConfig: NextConfig = {
  async rewrites() {
    const target = process.env.CHATUI_API_TARGET ?? "http://localhost:8000";
    return [
      { source: "/api/:path*", destination: `${target}/api/:path*` },
      { source: "/health", destination: `${target}/health` },
    ];
  },
};

export default nextConfig;
