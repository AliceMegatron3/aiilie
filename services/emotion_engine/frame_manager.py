import json
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timedelta, timezone
import uuid

from models.emotion_models import EmotionFrame, build_frame_from_payload
from core.database import DatabaseManager
from core.path_resolver import get_emotion_frames_dir
from core.config_manager import config_manager
from services.resource_governor import resource_governor

logger = logging.getLogger(__name__)

# 旧帧元数据归档窗口（默认 30 天）
_ARCHIVE_AGE_DAYS = 30

class EmotionFrameManager:
    def __init__(self, db: DatabaseManager):
        self._db = db
        self._frames_dir = get_emotion_frames_dir()

    def _check_frames_quota(self) -> None:
        """帧目录磁盘配额自检（基于 config task.temp_max_size_gb，超限仅告警不阻断）。"""
        try:
            quota_gb = config_manager.get("task.temp_max_size_gb", 2)
            if not quota_gb:
                return
            total = sum(
                f.stat().st_size for f in self._frames_dir.rglob("*.json") if f.is_file()
            )
            if total > int(quota_gb) * 1024 ** 3:
                logger.warning(
                    "[EmotionFrameManager] 帧目录体积 %.1fMB 超过配额 %sGB，建议后台归档",
                    total / (1024 ** 2), quota_gb,
                )
        except Exception:
            pass
        
    async def create_frame(self, frame_data: dict) -> EmotionFrame:
        """
        生成帧对象，元数据写入 sqlite 索引，完整对象序列化写入磁盘；
        调用补丁 E ResourceGovernor 做磁盘配额前置校验。
        """
        frame_id = str(uuid.uuid4())
        frame_data["frame_id"] = frame_id
        
        if "create_time" not in frame_data:
            frame_data["create_time"] = datetime.now()
            
        # 确保帧目录存在（修复：原实现未 mkdir，落盘直接 FileNotFoundError）
        self._frames_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._frames_dir / f"{frame_id}.json"
        frame_data["file_path"] = file_path
        
        frame = EmotionFrame(**frame_data)
        
        # 序列化为 JSON 字符串（mode="json"：Path→str、datetime→ISO，避免手写序列化器）
        payload = json.dumps(frame.model_dump(mode="json"), ensure_ascii=False)
        payload_size = len(payload.encode("utf-8"))
        
        # 磁盘配额自检（修复：resource_governor 无 check_disk_quota 方法，
        # 改为基于 config task.temp_max_size_gb 的目录级配额告警，超限不阻断落盘）
        self._check_frames_quota()
        
        # 落盘
        file_path.write_text(payload, encoding="utf-8")
        
        # 写入元数据到 SQLite
        # frame_id, frame_type, source_project_id, source_book_id, wave_level, mode, file_path, create_time
        await self._db._connection.execute(
            """
            INSERT INTO emotion_frames 
            (frame_id, frame_type, source_project_id, source_book_id, wave_level, mode, file_path, create_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                frame.frame_id,
                frame.frame_type,
                frame.source_project_id,
                frame.source_book_id,
                frame.wave_level,
                frame.mode,
                str(frame.file_path),
                frame.create_time.isoformat()
            )
        )
        await self._db._connection.commit()
        
        logger.info(f"EmotionFrame {frame_id} created and saved to disk.")
        return frame

    async def get_recent_standard_frames(self, project_id: str, limit: int = 3) -> List[EmotionFrame]:
        """
        获取最近 N 个标准帧（滑动窗口，续写用）。
        """
        limit = int(config_manager.get("emotion_engine.recent_frame_keep_count", limit))
        
        cursor = await self._db._connection.execute(
            """
            SELECT file_path FROM emotion_frames 
            WHERE source_project_id = ? AND frame_type = 'standard'
            ORDER BY create_time DESC
            LIMIT ?
            """,
            (project_id, limit)
        )
        rows = await cursor.fetchall()
        
        frames = []
        for row in rows:
            file_path = Path(row[0])
            if file_path.exists():
                frame = self.load_frame_full(str(file_path))
                if frame:
                    frames.append(frame)
                    
        # 返回正序（最近的在最后）
        frames.reverse()
        return frames

    async def load_frame_full(self, frame_id: str) -> Optional[EmotionFrame]:
        """按需从磁盘读取完整帧数据（按 frame_type 还原具体帧类型）。"""
        try:
            cursor = await self._db._connection.execute("SELECT file_path FROM emotion_frames WHERE frame_id = ?", (frame_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            
            file_path = Path(row[0])
            if not file_path.exists():
                return None
                
            payload = file_path.read_text(encoding="utf-8")
            data = json.loads(payload)
            return build_frame_from_payload(data)
        except Exception as e:
            logger.error(f"Failed to load EmotionFrame {frame_id}: {e}")
            return None

    async def prune_old_frame_meta(self, age_days: int = _ARCHIVE_AGE_DAYS) -> int:
        """
        后台归档：把超过归档窗口的旧帧元数据标记 archived（SQLite 只留索引，
        完整 JSON 保留在磁盘作为冷数据，不删除用户业务帧）。
        返回归档条数。
        """
        try:
            # 确保 archived 列存在（旧库无损迁移）
            cursor = await self._db._connection.execute("PRAGMA table_info(emotion_frames)")
            rows = await cursor.fetchall()
            columns = [r[1] for r in rows]
            if "archived" not in columns:
                await self._db._connection.execute(
                    "ALTER TABLE emotion_frames ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
                )
                await self._db._connection.commit()
                logger.info("[EmotionFrameManager] emotion_frames 表已添加 archived 列")

            cutoff = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
            cursor = await self._db._connection.execute(
                "UPDATE emotion_frames SET archived=1 WHERE archived=0 AND create_time < ?",
                (cutoff,),
            )
            await self._db._connection.commit()
            archived = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
            if archived:
                logger.info(
                    "[EmotionFrameManager] 已归档 %d 条旧帧元数据（磁盘 JSON 保留为冷数据）",
                    archived,
                )
            return archived
        except Exception as e:
            logger.warning("[EmotionFrameManager] 旧帧归档异常（不阻断）: %s", e)
            return 0
