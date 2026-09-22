"""客服全链路端到端冒烟（T137，D057）。

后端起在 localhost:8000（dev profile，真实 deepseek-flash + RAG + 案件库）后执行：

    uv run python scripts/smoke_support_e2e.py

覆盖：知识问答（RAG）→ 案件进度查询 → 转人工（工具标记→确定性流转）→
坐席工单队列 → 坐席回复（门户可见）→ 关闭（终态 409）。
LLM 问答环节为非断言输出（供人工核对），状态机环节为硬断言。
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx

BASE = "http://127.0.0.1:8000"


def _staff_headers() -> dict[str, str]:
    """坐席端点鉴权头（T147）：读 STAFF_KEYS 取首个 Key；未配置回退 compose 演示
    栈默认值（与 docker-compose.yml 对齐）。本地 dev 直跑无 Key 时服务端不校验。"""
    raw = os.environ.get("STAFF_KEYS") or "demo-staff:demo-key-2026"
    key = raw.split(",")[0].strip().partition(":")[2].strip()
    return {"X-Staff-Key": key} if key else {}


def _head(text: str | None, n: int = 100) -> str:
    if not text:
        return "(空)"
    return text.replace("\n", " ")[:n] + ("…" if len(text) > n else "")


async def main() -> int:
    async with httpx.AsyncClient(
        base_url=BASE, timeout=180.0, headers=_staff_headers()
    ) as c:
        health = (await c.get("/health")).json()
        print(f"[0] health: {health['status']} (profile={health['profile']})")

        conv = (await c.post("/api/v1/support/conversations")).json()
        cid = conv["conversation_id"]
        print(f"[1] 会话建立: {cid[:12]}… status={conv['status']}")

        # 知识问答：真实 RAG 检索 + deepseek-flash 组织回答
        r = (
            await c.post(
                f"/api/v1/support/conversations/{cid}/messages",
                json={"content": "阑尾炎住院理赔需要准备什么材料？有等待期吗？"},
            )
        ).json()
        print(f"[2] 知识问答（人工核对）: {_head(r.get('reply'), 160)}")

        # 案件进度查询：dev 库既有案件 CASE-2026-0001（received）
        r = (
            await c.post(
                f"/api/v1/support/conversations/{cid}/messages",
                json={"content": "帮我查一下案件 CASE-2026-0001 现在到哪一步了？"},
            )
        ).json()
        print(f"[3] 进度查询（人工核对）: {_head(r.get('reply'), 160)}")

        # 转人工
        r = (
            await c.post(
                f"/api/v1/support/conversations/{cid}/messages",
                json={"content": "你们处理太慢了，我要投诉，赶紧给我转人工！"},
            )
        ).json()
        assert r["status"] == "escalated", f"转人工失败: {r}"
        print(f"[4] 转人工: status=escalated reply={_head(r.get('reply'), 80)}")
        detail = (await c.get(f"/api/v1/support/conversations/{cid}")).json()
        print(f"    原因留痕: {detail['escalated_reason']}")

        # 坐席队列
        tickets = (await c.get("/api/v1/support/tickets")).json()
        assert tickets["total"] >= 1, "工单未入队"
        item = next(t for t in tickets["items"] if t["conversation_id"] == cid)
        print(f"[5] 坐席队列: {tickets['total']} 单，预览={_head(item['last_message']['content'], 60)}")

        # 坐席回复 → 门户可见
        await c.post(
            f"/api/v1/support/tickets/{cid}/reply",
            json={"agent": "agent-01", "content": "您好，我是人工坐席。已记录您的意见，正在核实案件进度，请稍候。"},
        )
        history = (await c.get(f"/api/v1/support/conversations/{cid}/messages")).json()
        agent_msgs = [m for m in history["items"] if m["role"] == "agent"]
        assert agent_msgs, "门户未见坐席回复"
        print(f"[6] 坐席回复门户可见: {len(agent_msgs)} 条")

        # 关闭 → 终态校验
        closed = (
            await c.post(
                f"/api/v1/support/tickets/{cid}/close",
                json={"agent": "agent-01", "note": "已向客户说明进度，投诉登记闭环。"},
            )
        ).json()
        assert closed["status"] == "closed"
        final = (await c.post(
            f"/api/v1/support/conversations/{cid}/messages", json={"content": "在吗"}
        )).status_code
        assert final == 409, f"终态应 409，实得 {final}"
        print(f"[7] 关闭终态: closed，门户再发消息 → {final}")

        tickets_after = (await c.get("/api/v1/support/tickets")).json()
        left = [t for t in tickets_after["items"] if t["conversation_id"] == cid]
        assert not left, "关闭后仍在队列"
        print("[8] 队列出清 ✓  全链路冒烟通过")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
