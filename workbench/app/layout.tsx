import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "claimflow 坐席工作台",
  description: "人工介入工单处理（转人工会话的上下文审阅与结论回写）",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
