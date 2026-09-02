"""T072 CI 评测门禁脚本测试：放行/拦截/无基线/坏报告，经 subprocess 验证退出码。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path("scripts/check_eval_gate.py")


def _write_report(path: Path, rate: float, total: int = 20) -> Path:
    passed = round(rate * total)
    path.write_text(
        json.dumps(
            {
                "summary": {
                    "task_completion_rate": rate,
                    "passed": passed,
                    "total": total,
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _run_gate(report: Path, baseline: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(report), "--baseline", str(baseline)],
        capture_output=True,
        text=True,
    )


def test_gate_passes_small_drop(tmp_path: Path) -> None:
    """降幅 ≤5pp 放行（88% → 84%）。"""
    base = _write_report(tmp_path / "base.json", 0.88, total=200)
    cur = _write_report(tmp_path / "cur.json", 0.84)
    result = _run_gate(cur, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "放行" in result.stdout


def test_gate_blocks_big_drop(tmp_path: Path) -> None:
    """降幅 >5pp 拦截（88% → 82%）。"""
    base = _write_report(tmp_path / "base.json", 0.88, total=200)
    cur = _write_report(tmp_path / "cur.json", 0.82)
    result = _run_gate(cur, base)
    assert result.returncode == 1
    assert "拦截" in result.stdout


def test_gate_no_baseline_passes(tmp_path: Path) -> None:
    """基线缺失不拦截（仅记录），报告可读。"""
    cur = _write_report(tmp_path / "cur.json", 0.5)
    result = _run_gate(cur, tmp_path / "missing.json")
    assert result.returncode == 0
    assert "不拦截" in result.stdout


def test_gate_invalid_report_fails(tmp_path: Path) -> None:
    """报告不可读/缺字段 → 退出码 1（明确失败而非静默放行）。"""
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    result = _run_gate(bad, tmp_path / "missing.json")
    assert result.returncode == 1


def test_wilson_ci_reasonable_width() -> None:
    """wilson_ci 口径抽查：n=200 p=88% 半宽约 ±4~5pp；n=0 安全返回。"""
    from evals.metrics import wilson_ci

    lo, hi = wilson_ci(176, 200)
    assert 0.82 <= lo <= 0.85
    assert 0.91 <= hi <= 0.93
    assert wilson_ci(0, 0) == [0.0, 1.0]
