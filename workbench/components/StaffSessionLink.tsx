"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

/** 导航栏坐席会话入口（T215/D092）：登录态由服务端 cookie 决定，此处只按 RSC 传入的状态渲染。 */
export default function StaffSessionLink({ loggedIn }: { loggedIn: boolean }) {
  const router = useRouter();

  async function onLogout() {
    await fetch("/staff-session", { method: "DELETE" });
    router.push("/login");
    router.refresh();
  }

  if (!loggedIn) {
    return (
      <Link
        href="/login"
        className="rounded-full px-3 py-1.5 text-[12px] font-medium text-cf-blue transition-colors hover:bg-black/[0.05]"
      >
        坐席登录
      </Link>
    );
  }
  return (
    <button
      type="button"
      onClick={onLogout}
      className="rounded-full px-3 py-1.5 text-[12px] font-medium text-cf-text-2 transition-colors hover:bg-black/[0.05]"
    >
      退出登录
    </button>
  );
}
