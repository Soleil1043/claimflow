"""services/skills.py 作业规程装载器测试（T081）。

覆盖：_shared 装载、险种回退链、缺失回退 base、format 与 skill 拼接顺序
（skill 内容可含大括号不受 format 影响）。
"""

from __future__ import annotations

from services.skills import build_system_prompt, load_skill


def test_load_shared_skill() -> None:
    """orchestrator/_shared.md 存在且可装载（T081 随任务落库的调度规程）。"""
    skill = load_skill("orchestrator", "_shared")
    assert skill is not None
    assert "调度" in skill or "并行" in skill


def test_line_fallback_to_shared() -> None:
    """险种文件不存在 → 回退 _shared.md（orchestrator 暂无 medical.md）。"""
    skill = load_skill("orchestrator", "medical")
    assert skill == load_skill("orchestrator", "_shared")


def test_missing_stage_returns_none() -> None:
    """阶段目录不存在 → None（调用方回退 base prompt 并告警）。"""
    assert load_skill("nonexistent_stage", "_shared") is None


def test_build_system_prompt_formats_then_appends_skill() -> None:
    """先 format 占位符再拼 skill：skill 内容可含大括号（few-shot JSON 等）。"""
    base = "调度指令：{snapshot}"

    class FakeSkills:
        """临时替换 SKILLS_DIR 指向的探测（直接断言拼接语义即可）。"""

    content = build_system_prompt(base, "orchestrator", "_shared", snapshot="S1")
    assert content.startswith("调度指令：S1")
    skill = load_skill("orchestrator", "_shared")
    assert skill is not None and content.endswith(skill)


def test_build_system_prompt_missing_skill_fallback_base() -> None:
    """skill 缺失 → 仅 base（占位符仍正确 format），并告警（不抛错）。"""
    content = build_system_prompt("BASE {v}", "nonexistent_stage", "_shared", v="42")
    assert content == "BASE 42"
