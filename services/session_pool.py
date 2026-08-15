import json
import logging
import uuid
import datetime
from pathlib import Path
from collections import OrderedDict
from typing import Optional, Dict
from models.session_models import SessionContext, SessionPoolStats
from utils.resource_path import get_user_data_path
from core.config_manager import config_manager
logger = logging.getLogger(__name__)
class ModelSessionPool:
    """补丁F扩展：多模型会话缓存池，支持LRU及落盘持久化。"""
    
    def __init__(self):
        self.max_memory = config_manager.get("session_pool.max_memory_session_count", 30)
        self.enable = config_manager.get("session_pool.enable", True)
        self.session_dir = get_user_data_path("data/sessions")
        self.session_dir.mkdir(parents=True, exist_ok=True)
        
        # LRU 热缓存 (session_id -> SessionContext)
        self._memory_cache: OrderedDict[str, SessionContext] = OrderedDict()
    def create_session(self, model_key: str, bind_project: str = None, 
                       bind_doc: str = None, bind_branch: str = None, 
                       ttl: int = 43200, max_msgs: int = 25) -> str:
        if not self.enable:
            return None

        # 防御性钳制（Pydantic 已校验，此处兜底直接调用场景）
        ttl = max(60, min(int(ttl or 43200), 30 * 24 * 3600))
        max_msgs = max(1, min(int(max_msgs or 25), 10000))

        session_id = str(uuid.uuid4())
        session = SessionContext(
            session_id=session_id,
            model_key=model_key,
            bind_project_id=bind_project,
            bind_doc_id=bind_doc,
            bind_branch_id=bind_branch,
            ttl_seconds=ttl,
            max_message_count=max_msgs
        )
        self._add_to_memory(session)
        self._save_to_disk(session)
        logger.info(f"✨ 创建会话: {session_id} (Model: {model_key}, Branch: {bind_branch})")
        return session_id
    def get_session(self, session_id: str) -> Optional[SessionContext]:
        if not self.enable or not session_id:
            return None
            
        if session_id in self._memory_cache:
            self._memory_cache.move_to_end(session_id)
            session = self._memory_cache[session_id]
            session.last_access_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            return session
            
        # 尝试从磁盘加载
        session_file = self.session_dir / f"{session_id}.json"
        if session_file.exists():
            try:
                with open(session_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    session = SessionContext(**data)
                    # 检查是否过期
                    last_acc = datetime.datetime.fromisoformat(session.last_access_at)
                    if (datetime.datetime.now(datetime.timezone.utc) - last_acc).total_seconds() > session.ttl_seconds:
                        self.destroy_session(session_id)
                        return None
                    session.last_access_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    self._add_to_memory(session)
                    return session
            except Exception as e:
                logger.error(f"加载磁盘会话失败 {session_id}: {e}")
        return None
    def append_message(self, session_id: str, role: str, content: str):
        session = self.get_session(session_id)
        if not session or session.frozen:
            return
            
        session.messages.append({"role": role, "content": content})
        
        # 裁剪超长消息
        if len(session.messages) > session.max_message_count:
            # 保留 system prompt (第一条), 并从前面截断
            sys_msgs = [m for m in session.messages if m["role"] == "system"]
            other_msgs = [m for m in session.messages if m["role"] != "system"]
            
            # 计算需要裁剪的非 system 消息数量
            trim_target = session.max_message_count - len(sys_msgs)
            if trim_target < 0:
                trim_target = 0
            if len(other_msgs) > trim_target:
                session.messages = sys_msgs + other_msgs[-trim_target:]
            else:
                session.messages = sys_msgs + other_msgs
            logger.info(f"✂️ 会话 {session_id} 超出最大轮数，已裁剪。")
        self._save_to_disk(session)
    def freeze_session(self, session_id: str):
        session = self.get_session(session_id)
        if session:
            session.frozen = True
            self._save_to_disk(session)
            logger.info(f"❄️ 会话 {session_id} 已冻结归档。")
    def destroy_session(self, session_id: str):
        self._memory_cache.pop(session_id, None)
        f = self.session_dir / f"{session_id}.json"
        if f.exists():
            f.unlink()
    def _add_to_memory(self, session: SessionContext):
        self._memory_cache[session.session_id] = session
        self._memory_cache.move_to_end(session.session_id)
        if len(self._memory_cache) > self.max_memory:
            # 驱逐最旧的
            evicted_id, evicted_session = self._memory_cache.popitem(last=False)
            self._save_to_disk(evicted_session)
            
    def _save_to_disk(self, session: SessionContext):
        f = self.session_dir / f"{session.session_id}.json"
        with open(f, 'w', encoding='utf-8') as out:
            json.dump(session.model_dump(), out, ensure_ascii=False, indent=2)
    def get_all_sessions(self) -> list:
        sessions = []
        if self.session_dir.exists():
            for f in self.session_dir.glob("*.json"):
                sid = f.stem
                sess = self.get_session(sid)
                if sess:
                    sessions.append(sess.model_dump())
        return sessions
    def get_stats(self) -> dict:
        total_sessions = 0
        frozen_sessions = 0
        hot_cache_size = len(self._memory_cache)
        total_messages = 0
        
        if self.session_dir.exists():
            for f in self.session_dir.glob("*.json"):
                total_sessions += 1
                try:
                    with open(f, 'r', encoding='utf-8') as file:
                        data = json.load(file)
                        if data.get("frozen"):
                            frozen_sessions += 1
                        total_messages += len(data.get("messages", []))
                except:
                    pass
                    
        return {
            "total_sessions": total_sessions,
            "hot_cache_sessions": hot_cache_size,
            "cold_cache_sessions": total_sessions - hot_cache_size,
            "frozen_sessions": frozen_sessions,
            "total_reused_messages": total_messages
        }
session_pool = ModelSessionPool()