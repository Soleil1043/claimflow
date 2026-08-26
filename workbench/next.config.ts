import type { NextConfig } from "next";

/**
 * dev/start 代理：前端 /api/* 直连本地后端 8000（A06/interventions），
 * 浏览器侧无跨域；后端地址可用 WORKBENCH_API_TARGET 覆盖。
 */
const nextConfig: NextConfig = {
  async rewrites() {
    const target = process.env.WORKBENCH_API_TARGET ?? "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${target}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
