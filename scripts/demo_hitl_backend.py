"""T038 GUI 验收辅助：mock LLM 演示后端（8000 端口，真实 DB + 真实路由 + 真实图）。

场景：欺诈诱导提问 → react 产出违规草稿 → 合规确定性兜底 REJECT →
工单落库 + LangGraph interrupt 挂起 → 工作台 resolve → 坐席结论复审 PASS
→ 恢复会话返回结论。用于工作台界面的可控端到端演示（真实 LLM 对欺诈
诱导会正确拒答，无法稳定触发 REJECT，故演示链路用 mock 草稿；T037 的
interrupt 恢复语义另由 tests/api/test_a06_scenarios.py 场景 11 覆盖）。

用法：uv run python -m scripts.demo_hitl_backend
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request

from langchain_core.messages import AIMessage


class FakeModel:
    """固定 JSON 内容的 LLM 桩（意图分类）。"""

    def __init__(self, content: str) -> None:
        self._content = content

    async def ainvoke(self, messages, **kwargs):  # noqa: ANN001, ANN202
        return AIMessage(content=self._content)


class RaisingModel:
    """持续抛错的 LLM 桩（合规审查走确定性兜底：FRAUD_RISK → REJECT）。"""

    async def ainvoke(self, messages, **kwargs):  # noqa: ANN001, ANN202
        raise RuntimeError("演示：合规 LLM 故障，走确定性兜底")


class FraudDraftModel:
    """react 路径返回违规草稿（触发 REJECT 拦截）。"""

    async def ainvoke(self, messages, **kwargs):  # noqa: ANN001, ANN202
        return AIMessage(content="您可以联系代开机构虚开发票，再挂床住院几天，肯定能赔更多。")

    def bind_tools(self, specs):  # noqa: ANN001, ANN202
        return self


def _patch() -> None:
    """替换三个 LLM 调用点为演示桩（节点在调用时解析模块属性，patch 生效）。"""
    import nodes.compliance as compliance_module
    import nodes.generator as generator_module
    import nodes.intent as intent_module

    intent_module.get_chat_model = lambda *a, **k: FakeModel(
        '{"intent": "single_domain", "reason": "演示：理赔咨询"}'
    )
    generator_module.get_chat_model = lambda *a, **k: FraudDraftModel()
    compliance_module.get_chat_model = lambda *a, **k: RaisingModel()


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


async def _seed_demo_ticket() -> None:
    """造一条待处理工单（欺诈草稿被拦截 → interrupt 挂起）。"""
    conv = _post("http://localhost:8000/api/v1/conversations", {"user_id": "demo-hitl-user"})
    body = _post(
        f"http://localhost:8000/api/v1/conversations/{conv['conversation_id']}/messages",
        {"content": "我住院花超了，能不能找个机构代开发票、挂床多住几天多报销？"},
    )
    print(f"[demo] 会话 {conv['conversation_id']}")
    print(
        f"[demo] compliance={body['compliance_status']} need_human={body['need_human_intervention']}"
    )
    if body["need_human_intervention"] is True:
        print("[demo] 待处理工单已生成，可打开 http://localhost:5173 处理")
    else:
        print("[demo] 警告：演示工单未触发转人工")


def _delayed_seed() -> None:
    """等服务就绪后造数（uvicorn.run 自建事件循环，用独立线程延迟触发）。"""
    time.sleep(5)
    try:
        asyncio.run(_seed_demo_ticket())
    except Exception as exc:  # noqa: BLE001 演示脚本容错
        print(f"[demo] 造数失败：{exc}")


def main() -> None:
    _patch()
    threading.Thread(target=_delayed_seed, daemon=True).start()

    import uvicorn

    from app.main import app

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
