"""skill 作业规程装载器（Phase 8 T081，D039）。

skills/<stage>/<line>.md 为各阶段×险种的作业规程（SOP / 红线清单 / few-shot / 工具规程），
装载后拼接到对应 worker / orchestrator 的 system prompt——**准确率迭代改文本不改代码**；
skill 变更经金样本回归（evals）验证后生效，可接 A/B 框架做新旧规程对比。

解析规则：
- line = 险种（medical/auto/...）或 "_shared"（跨险种共用）
- 查找顺序：<stage>/<line>.md → <stage>/_shared.md → None（回退 base prompt 并告警）
- 每次调用读盘：文件小，保证"改文本即时生效"的迭代体验
"""

from __future__ import annotations

from pathlib import Path

from app.core.logging import get_logger

log = get_logger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def load_skill(stage: str, line: str = "_shared") -> str | None:
    """装载指定阶段/险种的作业规程文本；缺失返回 None（调用方回退 base prompt）。"""
    if line == "_shared":
        candidates = [SKILLS_DIR / stage / "_shared.md"]
    else:
        candidates = [
            SKILLS_DIR / stage / f"{line}.md",
            SKILLS_DIR / stage / "_shared.md",
        ]
    for path in candidates:
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                log.warning("skill_read_failed", path=str(path), error=str(exc))
                return None
    log.warning("skill_missing_fallback_base", stage=stage, line=line)
    return None


def build_system_prompt(
    base_prompt: str, stage: str, line: str = "_shared", **fmt: str
) -> str:
    """组装 system prompt：base 占位符填充 → 拼接 skill（缺失回退 base 并告警）。

    先 format 再拼 skill：skill 文本可含任意大括号（few-shot JSON 等）不受 format 影响。
    """
    content = base_prompt.format(**fmt) if fmt else base_prompt
    skill = load_skill(stage, line)
    if skill:
        content = f"{content.rstrip()}\n\n# 作业规程（skill）\n\n{skill}"
    return content
