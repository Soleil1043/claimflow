"use client";

import { useEffect, useState } from "react";
import { checkHealth } from "@/lib/api";

type HealthState = "checking" | "up" | "down";

const VIEW: Record<HealthState, { cls: string; dot: string; label: string }> = {
  checking: { cls: "muted", dot: "cf-dot-warn", label: "检测中…" },
  up: { cls: "ok", dot: "cf-dot-ok", label: "后端已连接" },
  down: { cls: "err", dot: "cf-dot-err", label: "后端不可达" },
};

/** 头部后端健康状态 pill（挂载时探测一次，对齐 Gradio demo.load 行为） */
export function HealthPill() {
  const [state, setState] = useState<HealthState>("checking");

  useEffect(() => {
    let alive = true;
    checkHealth().then((ok) => {
      if (alive) setState(ok ? "up" : "down");
    });
    return () => {
      alive = false;
    };
  }, []);

  const v = VIEW[state];
  return (
    <span className={`cf-pill ${v.cls}`} style={{ whiteSpace: "nowrap" }}>
      <span className={v.dot} />
      {v.label}
    </span>
  );
}
