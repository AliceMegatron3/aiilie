import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel, Field

from core.path_resolver import get_app_data_dir, get_workspace_dir

logger = logging.getLogger(__name__)

class AgentExperience(BaseModel):
    id: str
    exp_type: str = Field(..., description="量化知识(quantize), 创作知识(creative), 工作知识(workflow)")
    content: str = Field(..., description="经验具体内容")
    status: str = Field(default="pending", description="pending 待审, approved 已批准, rejected 已拒绝")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class ExperienceManager:
    """管理智能体进化的三层经验库，并输出人类可读的本地日记。"""
    
    def __init__(self):
        self.exp_file = get_app_data_dir() / "agent_experiences.json"
        self._ensure_file()

    def _ensure_file(self):
        if not self.exp_file.exists():
            self.exp_file.write_text(json.dumps([]), encoding="utf-8")

    def _load_all(self) -> list[dict]:
        try:
            return json.loads(self.exp_file.read_text(encoding="utf-8"))
        except:
            return []

    def _save_all(self, data: list[dict]):
        self.exp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self._generate_report(data)

    def add_experience(self, exp_type: str, content: str) -> dict:
        import uuid
        exp = AgentExperience(
            id=str(uuid.uuid4()),
            exp_type=exp_type,
            content=content,
            status="pending"
        )
        data = self._load_all()
        data.append(exp.model_dump())
        self._save_all(data)
        logger.info("新增智能体待审经验: %s", content)
        return exp.model_dump()

    def get_experiences(self, status: str = None) -> list[dict]:
        data = self._load_all()
        if status:
            return [d for d in data if d.get("status") == status]
        return data

    def approve_experience(self, exp_id: str, is_approved: bool) -> bool:
        data = self._load_all()
        found = False
        for d in data:
            if d.get("id") == exp_id:
                d["status"] = "approved" if is_approved else "rejected"
                found = True
                break
        if found:
            self._save_all(data)
        return found

    def _generate_report(self, data: list[dict]):
        """生成供人类阅读的本地 Markdown 日记"""
        try:
            report_path = get_workspace_dir() / "Agent_Experience_Report.md"
            
            approved_data = [d for d in data if d.get("status") == "approved"]
            pending_data = [d for d in data if d.get("status") == "pending"]
            
            lines = [
                "# 🧠 智能体核心经验反思日记",
                "> 这是我的自我学习记录。我在此总结量化、创作和系统工作的经验。只有您批准（Approved）的经验，才会成为我的长期记忆。",
                ""
            ]
            
            # --- 待审区 ---
            if pending_data:
                lines.append("## 🟡 待审经验 (Pending) - 请主人批阅")
                for d in pending_data:
                    lines.append(f"- **[{d['exp_type'].upper()}]** {d['content']} *(ID: {d['id']})*")
                lines.append("")
                
            # --- 已归档区 ---
            lines.append("## 🟢 已归档核心记忆 (Approved)")
            
            types = {"quantize": "量化知识", "creative": "创作知识", "workflow": "工作知识"}
            for t_key, t_name in types.items():
                lines.append(f"### {t_name}")
                items = [d for d in approved_data if d.get("exp_type") == t_key]
                if not items:
                    lines.append("*暂无记录*")
                for d in items:
                    lines.append(f"- {d['content']}")
                lines.append("")

            report_path.write_text("\n".join(lines), encoding="utf-8")
        except Exception as e:
            logger.error("生成经验日记失败: %s", e)

# 全局实例
experience_manager = ExperienceManager()
