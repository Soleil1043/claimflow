import { redirect } from "next/navigation";

/** v1 会话工单已随 T093 后端删除（T104 清理）——坐席直达核赔工单列表。 */
export default function Home() {
  redirect("/cases");
}
