"""黑名单查询工具（F06）：按持有人证件号查询欺诈黑名单。

数据源：data/mock/blacklist.json（Mock 起步）；生产替换为风控系统 API Adapter，
函数签名不变。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import PrivateAttr

from schemas.tools import ToolInput
from tools.base import ClaimflowTool

BLACKLIST_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "mock" / "blacklist.json"


def query_blacklist_by_id(id_card: str) -> dict[str, Any]:
    """纯函数：按证件号查黑名单。返回 {"blacklisted": bool, "reason": str|None}。"""
    try:
        entries = json.loads(BLACKLIST_PATH.read_text(encoding="utf-8"))
    except OSError:
        # 名单文件缺失按"无黑名单"处理（fail-open，风控只是降级信号之一）
        return {"blacklisted": False, "reason": None}
    for entry in entries:
        if entry.get("id_card") == id_card:
            return {
                "blacklisted": True,
                "reason": str(entry.get("reason") or "命中欺诈黑名单"),
            }
    return {"blacklisted": False, "reason": None}


class BlacklistQueryInput(ToolInput):
    """黑名单查询入参。"""

    id_card: str


class QueryBlacklistTool(ClaimflowTool):
    name: str = "query_blacklist"
    description: str = (
        "按持有人身份证号查询欺诈黑名单。返回 blacklisted 与命中原因；"
        "风控筛查、投保前校验场景使用。"
    )
    args_schema: type[BlacklistQueryInput] = BlacklistQueryInput

    _blacklist_path: Path | None = PrivateAttr(default=None)

    def __init__(self, blacklist_path: Path | None = None, **kwargs: Any) -> None:
        """可注入名单路径（测试用），缺省 data/mock/blacklist.json。"""
        super().__init__(**kwargs)
        self._blacklist_path = blacklist_path

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(f"{self.name} 仅支持异步调用（ainvoke）")

    async def _arun(self, id_card: str) -> dict[str, Any]:
        path = self._blacklist_path or BLACKLIST_PATH
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            return {"blacklisted": False, "reason": None}
        for entry in entries:
            if entry.get("id_card") == id_card:
                return {
                    "blacklisted": True,
                    "reason": str(entry.get("reason") or "命中欺诈黑名单"),
                }
        return {"blacklisted": False, "reason": None}
