import type { Metadata } from "next";
import { HealthPill } from "@/components/HealthPill";
import "./globals.css";

export const metadata: Metadata = {
  title: "保险理赔智能助手",
  description: "多智能体理赔对话系统 · Orchestrator-Worker",
};

/**
 * 全站骨架（T063 / D030 设计语言）：sticky 毛玻璃浮层导航 + 纵向 flex 内容区。
 * 与 workbench 同构；健康状态 pill 为客户端组件（挂载探测 /health）。
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="flex min-h-dvh flex-col">
        <header className="cf-chrome sticky top-0 z-40">
          <div className="mx-auto flex w-full max-w-3xl items-center justify-between gap-3 px-4 py-3">
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-cf-blue text-sm font-bold text-white shadow-sm">
                CF
              </div>
              <div>
                <div className="text-[15px] font-semibold leading-tight tracking-tight">
                  保险理赔智能助手
                </div>
                <div className="text-[11px] leading-tight text-cf-text-2">
                  多智能体理赔对话系统 · Orchestrator-Worker
                </div>
              </div>
            </div>
            <HealthPill />
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
