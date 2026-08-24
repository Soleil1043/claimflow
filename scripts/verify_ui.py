"""T014 验收脚本：模拟界面回调（BackendClient + chat）真实走通一轮。"""

import asyncio

import ui.app as ui_app


async def main() -> None:
    # 模拟界面状态
    session_state: dict = {}
    history: list = []

    # 第一轮：触发会话创建 + 工具链
    reply = await ui_app.chat("保单 POL-2025-0001 住院花了15800元能赔多少？", history, session_state)
    print("=== 第一轮回复（前 300 字）===")
    print(reply[:300])
    assert "conversation_id" in session_state, "会话未创建"
    assert "4,640" in reply or "4640" in reply, "回复中未包含预期赔付金额"
    assert "policy_query" in reply and "claim_calculator" in reply, "工具轨迹缺失"

    # 第二轮：多轮上下文
    reply2 = await ui_app.chat("那份保单的免赔额是多少？", history, session_state)
    print("=== 第二轮回复（前 150 字）===")
    print(reply2[:150])
    assert "10,000" in reply2 or "10000" in reply2 or "1万" in reply2, "第二轮未正确引用上下文"

    print("T014 验收通过：界面回调 → 后端 API → 工具链 → 多轮，全链路 OK")


asyncio.run(main())
