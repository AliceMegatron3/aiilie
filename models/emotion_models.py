"""
models/emotion_models.py — 九阶情感量化引擎 Pydantic 模型（补丁G完整落地）
=========================================================================
帧三级体系：MicroFrame（微帧/瞬时）/ StandardFrame（标准帧/场景）
/ MacroFrame（宏帧/全书）。所有帧：
  - SQLite 只保存元数据索引（frame_type/wave_level/mode/file_path 等）；
  - 完整 JSON 帧落磁盘文件，禁止大 JSON 存入数据库行。

EmotionFrame 保留为兼容基类（旧调用方不变）。
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EmotionFrame(BaseModel):
    """情感帧兼容基类（旧接口签名不变）。"""
    frame_id: str
    frame_type: Literal["micro", "standard", "macro"]
    source_project_id: Optional[str] = None
    source_book_id: Optional[str] = None
    source_chapter: Optional[str] = None
    text_snippet: str = ""
    file_path: Optional[Path] = None
    sequence: int = 0

    # 三路情绪（九阶：1~9）
    scene_emotion: dict = Field(default_factory=dict)          # 场景情绪（张力/氛围）
    character_inner_emotion: dict = Field(default_factory=dict)  # 人物内心情绪
    conflict_emotion: dict = Field(default_factory=dict)       # 冲突情绪（强度/对抗）

    wave_level: int = Field(default=5, ge=1, le=9)
    mode: Literal["single", "mixed", "chaotic"] = "mixed"
    create_time: datetime = Field(default_factory=_now)
    archived: bool = False


class MicroFrame(EmotionFrame):
    """微帧：单句/瞬时情绪采样。"""
    frame_type: Literal["micro"] = "micro"


class StandardFrame(EmotionFrame):
    """标准帧：场景级三路情绪完整量化。"""
    frame_type: Literal["standard"] = "standard"


class MacroFrame(EmotionFrame):
    """宏帧：整书/整章的宏观情绪曲线汇总。"""
    frame_type: Literal["macro"] = "macro"


_FRAME_TYPE_MAP = {
    "micro": MicroFrame,
    "standard": StandardFrame,
    "macro": MacroFrame,
}


def build_frame_from_payload(payload: dict) -> EmotionFrame:
    """按 frame_type 构造对应帧类型（未知类型回退 EmotionFrame）。"""
    cls = _FRAME_TYPE_MAP.get(payload.get("frame_type"))
    if cls is None:
        return EmotionFrame(**payload)
    return cls(**payload)
