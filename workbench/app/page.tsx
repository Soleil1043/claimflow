import { redirect } from "next/navigation";

/** 根路径重定向：坐席直达核赔工单列表。 */
export default function Home() {
  redirect("/cases");
}
