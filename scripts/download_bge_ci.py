"""CI 专用 BGE-M3 获取器（T156）：缓存校验 → hub（镜像/官方）→ ModelScope 兜底。

从 CI YAML 剥成仓库脚本的原因：内联多行 Python 的缩进陷阱 + 需要本地可测。
下载失败的三重实锤背景：官方源对 Azure 出口 IP 重限流（匿名 429 秒拒）、
hf-mirror 对数据中心 IP 不稳、容器内在线下载受健康窗牵连——故在 runner 侧
先落齐产物再灌卷，init/app 零网络依赖。

通道说明：HF_ENDPOINT 在 huggingface_hub import 时读取，进程内轮换无效——
故 hub 通道的端点由 CI 步骤按尝试顺序设环境变量，每次独立进程。ModelScope
通道（阿里 CDN，匿名可用）下载目录平铺拷为 snapshots/manual——消费方
EMBEDDING_MODEL_PATH 只认目录内容，不依赖 refs/blobs 结构。成功产物由
actions/cache 持久化（job 成功后 post 保存），后续 run 离线秒过。

用法：uv run python -m scripts.download_bge_ci [--channel auto|hub|modelscope] [--check]
退出码：0=快照可用，1=获取失败（各尝试的原始异常已打 stderr）。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys

REPO_ID = "BAAI/bge-m3"
_HF_CACHE = pathlib.Path(os.environ.get("CI_HF_CACHE", "~/.cache/huggingface")).expanduser()
_SNAP_ROOT = _HF_CACHE / "hub" / "models--BAAI--bge-m3" / "snapshots"


_WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")


def _verify() -> bool:
    """快照完整性：任一快照目录含权重文件即可用（EMBEDDING_MODEL_PATH 口径）。

    HF 官方仓是 model.safetensors；ModelScope 镜像仓只有 pytorch_model.bin
    （本地下载清单实测，无 safetensors）——两种权重 transformers 均可离线加载。
    """
    hits = [h for w in _WEIGHT_FILES for h in _SNAP_ROOT.glob(f"*/{w}")]
    for hit in hits:
        print(f"[download_bge] snapshot ok: {hit}")
    return bool(hits)


def _hub_download() -> bool:
    """经 huggingface_hub 下载（HF_ENDPOINT/HF_TOKEN 由调用环境决定，import 时生效）。"""
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(REPO_ID)
    except Exception as exc:  # noqa: BLE001 —— 各尝试的失败是预期路径，交由下一手
        print(f"[download_bge] hub download failed: {exc}", file=sys.stderr)
        return False
    return _verify()


def _modelscope_download() -> bool:
    """ModelScope 兜底（阿里 CDN，匿名可用）：下载目录平铺拷为 snapshots/manual。"""
    try:
        from modelscope import snapshot_download as ms_download
    except ImportError:
        print("[download_bge] modelscope 未安装（CI 步骤负责预装），跳过该通道", file=sys.stderr)
        return False

    # 只取 配置/tokenizer/pytorch 权重——仓里还有 2.27G onnx 变体纯属浪费；
    # 旧版 SDK 不认 allow_patterns（TypeError）则退全量
    try:
        src = pathlib.Path(
            ms_download(REPO_ID, allow_patterns=["*.json", "*.model", "*.bin", "*.pt"])
        )
    except TypeError:
        try:
            src = pathlib.Path(ms_download(REPO_ID))
        except Exception as exc:  # noqa: BLE001
            print(f"[download_bge] modelscope download failed: {exc}", file=sys.stderr)
            return False
    except Exception as exc:  # noqa: BLE001
        print(f"[download_bge] modelscope download failed: {exc}", file=sys.stderr)
        return False

    manual = _SNAP_ROOT / "manual"
    manual.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(f, manual / f.name)
    print(f"[download_bge] modelscope files -> {manual}")
    return _verify()


def main() -> int:
    parser = argparse.ArgumentParser(description="CI BGE-M3 获取器")
    parser.add_argument(
        "--channel",
        choices=("auto", "hub", "modelscope"),
        default="auto",
        help="auto=校验→hub→modelscope 级联；hub/modelscope=单通道（端点由环境决定）",
    )
    parser.add_argument("--check", action="store_true", help="只校验本地快照，不下载")
    args = parser.parse_args()

    if args.check:
        return 0 if _verify() else 1

    if _verify():
        print("[download_bge] cache hit")
        return 0

    if args.channel in ("auto", "hub") and _hub_download():
        return 0
    if args.channel in ("auto", "modelscope") and _modelscope_download():
        return 0

    print("[download_bge] channel(s) failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
