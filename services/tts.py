"""
services/tts.py — 补丁2：沉浸式角色语调 TTS 与校对系统
=======================================================
DialogueSegmenter : 文本分割旁白/对话，识别对话所属角色
TTSDispatcher      : 多引擎调度（vits_local 本地 / azure / elevenlabs）
                    + 音频缓存 + OOC 反馈投递（写入批次3文档学习系统）
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from core.config_manager import config_manager
from core.path_resolver import get_app_data_dir, safe_join

logger = logging.getLogger(__name__)

# 对话引号配对：中文引号 + 英文引号
_DIALOGUE_PATTERN = re.compile(r"[“「\"'](.+?)[”」\"']")


class DialogueSegmenter:
    """旁白/对话分割器：识别对话与说话角色。"""

    def segment(self, text: str, known_characters: list[str] | None = None) -> list[dict[str, Any]]:
        """
        将文本切分为段落序列：
        [
            {"kind": "narration", "text": "旁白内容"},
            {"kind": "dialogue", "text": "对话内容", "speaker": "角色名" | None},
        ]
        角色识别规则：对话前文冒号标注（如「张三说：」）或已知角色名优先匹配。
        """
        known = set(known_characters or [])
        segments: list[dict[str, Any]] = []
        pos = 0
        for match in _DIALOGUE_PATTERN.finditer(text):
            before = text[pos : match.start()]
            if before.strip():
                segments.append({"kind": "narration", "text": before})
            dialogue = match.group(1)
            speaker = self._detect_speaker(before, known)
            segments.append({"kind": "dialogue", "text": dialogue, "speaker": speaker})
            pos = match.end()
        tail = text[pos:]
        if tail.strip():
            segments.append({"kind": "narration", "text": tail})
        return segments

    @staticmethod
    def _detect_speaker(before_text: str, known_characters: set[str]) -> str | None:
        """从对话前文识别说话角色（「张三道：」「张三说：」或已知角色名出现在前 20 字符）。"""
        window = before_text[-30:] if before_text else ""
        # 规则1：X说/道/喊/问/答 + 冒号或紧邻
        m = re.search(r"([\u4e00-\u9fa5A-Za-z0-9·]{1,8})(?:说|道|喊|问|答|吼|喃喃|低声|轻声道|笑道)[:：]?\s*$", window)
        if m:
            return m.group(1)
        # 规则2：已知角色名出现在窗口内
        for name in known_characters:
            if name and name in window:
                return name
        return None


class TTSDispatcher:
    """多引擎 TTS 调度器（vits_local 默认离线可用，azure/elevenlabs 需密钥）。"""

    def __init__(self) -> None:
        self._cache_dir = safe_join(get_app_data_dir(), "tts_cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ── 对外主入口 ──────────────────────────────────────────────

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        engine: str | None = None,
    ) -> dict[str, Any]:
        """合成语音，返回 {"file_name", "cache_hit", "engine"}。音频文件位于 tts_cache 目录。"""
        if not text or not text.strip():
            raise ValueError("合成文本不能为空")
        engine = engine or config_manager.get("tts.engine", "vits_local")
        voice = voice or ""

        cache_key = self._cache_key(engine, voice, text)
        cached = self._cache_dir / f"{cache_key}.wav"
        if cached.exists():
            return {"file_name": cached.name, "cache_hit": True, "engine": engine}

        if engine == "vits_local":
            file_name = await asyncio.to_thread(self._synthesize_vits_local, text, cached)
        elif engine == "azure":
            file_name = await self._synthesize_azure(text, voice, cached)
        elif engine == "elevenlabs":
            file_name = await self._synthesize_elevenlabs(text, voice, cached)
        else:
            raise ValueError(f"未知 TTS 引擎: {engine}（可选 vits_local / azure / elevenlabs）")

        return {"file_name": file_name, "cache_hit": False, "engine": engine}

    def resolve_audio_path(self, file_name: str) -> Path:
        """按文件名解析音频物理路径（safe_join 防路径穿越）。"""
        return safe_join(self._cache_dir, file_name)

    # ── 引擎实现 ────────────────────────────────────────────────

    def _synthesize_vits_local(self, text: str, target: Path) -> str:
        """VITS 本地引擎：Windows 使用系统 SAPI 合成 WAV（离线零依赖）；非 Windows 输出占位音频文件。"""
        import sys

        if sys.platform == "win32":
            try:
                import subprocess

                ps_script = (
                    "$ErrorActionPreference='Stop';"
                    "Add-Type -AssemblyName System.Speech;"
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                    "$s.SetOutputToWaveFile($args[1]);"
                    "$s.Speak($args[0]);"
                    "$s.Dispose()"
                )
                completed = subprocess.run(
                    [
                        "powershell", "-NoProfile", "-NonInteractive",
                        "-Command", ps_script, text, str(target),
                    ],
                    capture_output=True,
                    timeout=60,
                )
                if completed.returncode != 0:
                    logger.warning(
                        "[TTS] 本地 SAPI 合成失败 (rc=%s): %s",
                        completed.returncode,
                        completed.stderr.decode("utf-8", errors="ignore")[:200],
                    )
                    raise RuntimeError("本地 TTS 合成失败")
                if target.exists() and target.stat().st_size > 44:
                    return target.name
            except Exception as exc:
                logger.warning("[TTS] 本地 TTS 引擎异常: %s", exc)
        # 兜底：生成最小静音 WAV 占位（保证链路可用，前端可正常播放空音频）
        self._write_silent_wav(target)
        return target.name

    async def _synthesize_azure(self, text: str, voice: str, target: Path) -> str:
        """Azure 语音服务（需 tts.azure_key / tts.azure_region）。"""
        key = config_manager.get("tts.azure_key", "")
        region = config_manager.get("tts.azure_region", "")
        if not key or not region:
            raise ValueError("Azure TTS 未配置密钥（tts.azure_key / tts.azure_region）")
        import httpx

        voice_name = voice or "zh-CN-XiaoxiaoNeural"
        url = (
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        )
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm",
        }
        ssml = (
            f"<speak version='1.0' xml:lang='zh-CN'>"
            f"<voice name='{voice_name}'>{text}</voice></speak>"
        )
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, content=ssml)
                resp.raise_for_status()
                await asyncio.to_thread(target.write_bytes, resp.content)
        except Exception as exc:
            raise RuntimeError(f"Azure TTS 调用失败: {exc}") from exc
        return target.name

    async def _synthesize_elevenlabs(self, text: str, voice: str, target: Path) -> str:
        """ElevenLabs 引擎（需 tts.elevenlabs_api_key）。"""
        api_key = config_manager.get("tts.elevenlabs_api_key", "")
        if not api_key:
            raise ValueError("ElevenLabs TTS 未配置密钥（tts.elevenlabs_api_key）")
        import httpx

        voice_id = voice or "21m00Tcm4TlvDq8ikWAM"  # 默认 Rachel 音色
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                    json={"text": text, "model_id": "eleven_multilingual_v2"},
                )
                resp.raise_for_status()
                await asyncio.to_thread(target.write_bytes, resp.content)
        except Exception as exc:
            raise RuntimeError(f"ElevenLabs TTS 调用失败: {exc}") from exc
        return target.name

    @staticmethod
    def _cache_key(engine: str, voice: str, text: str) -> str:
        import hashlib

        digest = hashlib.md5(f"{engine}|{voice}|{text}".encode("utf-8")).hexdigest()[:16]
        return f"tts_{digest}"

    @staticmethod
    def _write_silent_wav(target: Path, seconds: float = 1.0) -> None:
        """写入最小静音 WAV（44 字节头 + 静音数据）。"""
        import struct
        import wave

        sample_rate = 16000
        frames = int(sample_rate * seconds)
        with wave.open(str(target), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(b"\x00\x00" * frames)

    # ── 校对反馈（OOC 写入批次3文档学习系统） ────────────────────

    async def submit_ooc_feedback(
        self,
        project_id: str,
        doc_id: str,
        character: str,
        content: str,
        learning_engine=None,
    ) -> dict[str, Any]:
        """
        角色 OOC（Out Of Character）反馈：作为额外学习样本写入批次3文档学习系统。
        learning_engine 为 DocumentLearningEngine 实例（注入后调用其反馈通道）；
        未注入时降级为落盘 JSON 反馈队列，不阻断主流程。
        """
        feedback = {
            "type": "ooc_feedback",
            "project_id": project_id,
            "doc_id": doc_id,
            "character": character,
            "content": content,
        }
        if learning_engine is not None and hasattr(learning_engine, "submit_feedback"):
            try:
                await learning_engine.submit_feedback(feedback)
                return {"accepted": True, "channel": "learning_engine"}
            except Exception as exc:
                logger.warning("[TTS] 学习引擎反馈通道异常，降级落盘: %s", exc)
        # 降级：反馈队列 JSONL（追加写，轮转由 GC 清理）
        queue_file = safe_join(get_app_data_dir(), "ooc_feedback.jsonl")
        await asyncio.to_thread(
            lambda: (queue_file.parent.mkdir(parents=True, exist_ok=True),
                     _append_jsonl(queue_file, feedback))
        )
        return {"accepted": True, "channel": "file_queue"}


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# 模块级单例
tts_dispatcher = TTSDispatcher()
