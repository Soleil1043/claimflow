import Link from "next/link";
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "claimflow 坐席工作台",
  description: "人工介入工单处理（转人工会话的上下文审阅与结论回写）",
};

/**
 * 全站骨架（T057 / D030）：sticky 毛玻璃浮层导航 + 内容区。
 * 导航为半透明材质（backdrop-filter），内容从其下滚过（Apple 浮层 chrome 模式）。
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen">
        <header className="cf-chrome sticky top-0 z-40">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-cf-blue text-sm font-bold text-white shadow-sm">
                CF
              </div>
              <div>
                <div className="text-[15px] font-semibold leading-tight tracking-tight">
                  claimflow 坐席工作台
                </div>
                <div className="text-[11px] leading-tight text-cf-text-2">
                  核赔工单处理 · 人工复核签批与补件
                </div>
              </div>
            </div>
            <nav className="hidden items-center gap-1 text-[12px] sm:flex">
              <Link
                href="/cases"
                className="rounded-full px-3 py-1.5 font-medium text-cf-text transition-colors hover:bg-black/[0.05]"
              >
                核赔工单
              </Link>
              <Link
                href="/"
                className="rounded-full px-3 py-1.5 font-medium text-cf-text-2 transition-colors hover:bg-black/[0.05]"
              >
                会话工单
              </Link>
            </nav>
            <div className="hidden text-[11px] text-cf-text-2 md:block">
              后端代理 → localhost:8000
            </div>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
