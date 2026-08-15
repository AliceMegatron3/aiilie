import re
import logging
import hashlib
from typing import List, Dict

logger = logging.getLogger(__name__)

class DialogueSegmenter:
    def segment(self, text: str) -> List[Dict[str, str]]:
        """
        基于正则的轻量级对话与旁白拆分器。
        返回格式: [{"type": "narration", "text": "..."}, {"type": "dialogue", "text": "...", "speaker": "未知"}]
        """
        segments = []
        # 简单匹配中文引号内的内容
        pattern = r'“([^”]+)”'
        
        last_idx = 0
        for match in re.finditer(pattern, text):
            # 旁白部分
            if match.start() > last_idx:
                segments.append({
                    "type": "narration",
                    "text": text[last_idx:match.start()].strip(),
                    "speaker": "旁白"
                })
                
            # 对话部分
            # 进阶实现中，这里应调用 NER 或轻量级大模型识别 speaker
            segments.append({
                "type": "dialogue",
                "text": match.group(1).strip(),
                "speaker": "未知角色" # 需结合上下文推断
            })
            last_idx = match.end()
            
        if last_idx < len(text):
            segments.append({
                "type": "narration",
                "text": text[last_idx:].strip(),
                "speaker": "旁白"
            })
            
        return [s for s in segments if s["text"]]

class TTSService:
    def __init__(self, voice_profiles: Dict[str, str]):
        self.profiles = voice_profiles
        self.default_voice = "voice_zh-CN-YunxiNeural" # 默认旁白音色

    async def generate_audio(self, text: str, speaker: str) -> str:
        """
        模拟调用第三方 TTS 服务 (如 Azure/Edge TTS)。
        返回生成的音频 URL 或 Base64 字符串。
        """
        voice_id = self.profiles.get(speaker, self.default_voice)
        logger.info(f"正在调用 TTS API. 文本: '{text[:10]}...', 音色: {voice_id}")
        
        # 真实实现应使用 httpx 调用云端 API 并将音频落地为本地文件
        # 此处返回 mock 的 URL
        # 用 md5 稳定哈希（跨进程一致），替代受 PYTHONHASHSEED 影响的 hash()
        digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
        mock_audio_url = f"/assets/audio/mock_{digest}.mp3"
        return mock_audio_url
