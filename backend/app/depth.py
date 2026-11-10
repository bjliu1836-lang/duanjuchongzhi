"""Depth Anything V2 Small 本地深度图推理。

懒加载单例 + 线程锁；CPU 推理（device=-1）。模型权重（约 100MB，Apache 2.0）
首次使用时从 HuggingFace 下载到默认缓存目录，之后离线可用。
"""
from __future__ import annotations

import threading
from pathlib import Path

MODEL_ID = "depth-anything/Depth-Anything-V2-Small-hf"

_pipeline = None
_lock = threading.Lock()


def _get_pipeline():
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    with _lock:
        if _pipeline is None:
            from transformers import pipeline

            _pipeline = pipeline("depth-estimation", model=MODEL_ID, device=-1)
    return _pipeline


def render_depth_map(frame_path: str, depth_path: str) -> None:
    """对单张关键帧生成 8-bit 灰度深度图（亮=近、暗=远），与原图同尺寸。"""
    from PIL import Image

    pipe = _get_pipeline()
    image = Image.open(frame_path).convert("RGB")
    depth = pipe(image)["depth"]
    Path(depth_path).parent.mkdir(parents=True, exist_ok=True)
    depth.save(depth_path)
