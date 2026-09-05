"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * 受理后自动轮询：异步交付（T103）下详情页在非终态时定期刷新服务端组件，
 * 终态（auto_issued/closed/referred/supplement_pending）或超时后停止。
 */
export default function AutoRefresh({
  status,
  jobStatus,
}: {
  status: string;
  jobStatus?: string;
}) {
  const router = useRouter();
  // 非终态，或交付任务在飞（如补件上传后 resume 重跑中）都保持刷新
  const active =
    status === "received" ||
    status === "in_progress" ||
    jobStatus === "queued" ||
    jobStatus === "running";

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => router.refresh(), 2000);
    const stop = setTimeout(() => clearInterval(timer), 120_000);
    return () => {
      clearInterval(timer);
      clearTimeout(stop);
    };
  }, [active, router]);

  if (!active) return null;
  return (
    <div className="flex items-center gap-2 text-[12px] text-cf-text-2">
      <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-cf-blue" />
      核赔进行中，页面自动刷新…
    </div>
  );
}
