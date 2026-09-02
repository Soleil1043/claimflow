import { ChatView } from "@/components/ChatView";

/** 对话主界面（T063）：全高纵向布局——消息滚动区 + 底部示例 chips 与 composer。 */
export default function Home() {
  return (
    <main className="mx-auto flex h-full min-h-0 w-full max-w-3xl flex-1 flex-col">
      <ChatView />
    </main>
  );
}
