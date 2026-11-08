"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/**
 * 坐席登录（T215/I12，D092）：Key 提交到本工作台 /staff-session，校验通过后由服务端
 * 写 HttpOnly cookie——页面脚本此后无法读取 Key。后端未配 staff_keys（dev 开放态）
 * 时无需登录即可使用。
 */
export default function LoginPage() {
  const router = useRouter();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const resp = await fetch("/staff-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
      });
      if (!resp.ok) {
        const data = (await resp.json().catch(() => ({}))) as { detail?: string };
        setError(data.detail ?? `登录失败（${resp.status}）`);
        return;
      }
      setKey("");
      router.push("/cases");
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-md px-6 py-16">
      <form onSubmit={onSubmit} className="cf-card space-y-4 p-6">
        <div>
          <h1 className="text-[17px] font-semibold">坐席登录</h1>
          <p className="mt-1 text-[12px] text-cf-text-2">
            输入分配给你的坐席 Key。Key 仅保存在本浏览器的安全 Cookie 中，页面脚本不可读取。
          </p>
        </div>
        <input
          type="password"
          autoComplete="current-password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="坐席 Key"
          className="cf-input w-full"
        />
        {error && <div className="text-[12px] text-cf-red">{error}</div>}
        <button type="submit" disabled={busy || !key.trim()} className="cf-btn primary w-full">
          {busy ? "校验中…" : "登录"}
        </button>
      </form>
    </main>
  );
}
