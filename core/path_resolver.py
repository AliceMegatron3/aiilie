"""
core/path_resolver.py — Windows路径解析与PyInstaller资源隔离
=============================================================
核心职责：
1. 检测运行环境（开发模式 vs PyInstaller打包模式）
2. 提供统一的路径解析接口，确保 pathlib.Path 全程使用
3. 管理 %APPDATA%/No0_AI_V4/ 用户数据目录
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
# ── 应用常量 ──────────────────────────────────────────────────────
APP_NAME = "No0_AI_V4"
APP_DATA_DIR_NAME = "data"
APP_LOG_DIR_NAME = "logs"
APP_TEMP_DIR_NAME = "temp"
def is_frozen() -> bool:
    """判断当前是否运行在 PyInstaller 打包环境中。"""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
def get_bundle_dir() -> Path:
    """
    获取打包后的资源根目录。
    - PyInstaller onefile 模式：sys._MEIPASS（临时解压目录）
    - 开发模式：项目根目录（本文件所在目录的上级）
    """
    if is_frozen():
        # PyInstaller 单文件模式下，静态资源解压到 sys._MEIPASS
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # 开发模式：core/ 的上级目录即为项目根
    return Path(__file__).resolve().parent.parent
def get_app_data_dir() -> Path:
    """
    获取用户数据持久化目录（Windows: %APPDATA%/No0_AI_V4/data/）。
    该目录不随 PyInstaller 临时目录销毁，适合存放 SQLite 数据库、日志等。
    自动创建目录（含中间层）。
    """
    # Windows 环境使用 %APPDATA%，回退到用户主目录
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata) / APP_NAME
    else:
        base = Path.home() / f".{APP_NAME}"
    data_dir = base / APP_DATA_DIR_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir
def get_log_dir() -> Path:
    """获取日志目录，位于用户数据目录下的 data/logs/ 子目录。"""
    log_dir = get_app_data_dir() / APP_LOG_DIR_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir
def get_temp_root() -> Path:
    """
    获取临时文件根目录。
    位于用户数据目录下的 data/temp/ 子目录，避免使用系统 %TEMP%
    以防止权限问题和路径过长（Windows MAX_PATH 限制）。
    """
    temp_dir = get_app_data_dir() / APP_TEMP_DIR_NAME
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir

def get_emotion_frames_dir() -> Path:
    """
    获取情感帧的持久化存储目录。
    """
    from core.config_manager import config_manager
    # 默认回退到 appdata/emotion_frames
    default_dir = get_app_data_dir() / "emotion_frames"
    dir_str = config_manager.get("paths.emotion_frames_dir", str(default_dir))
    
    # 将相对路径基于 app_data_dir 转换
    if dir_str.startswith("./") or dir_str.startswith(".\\"):
        frames_dir = get_app_data_dir() / dir_str[2:]
    else:
        frames_dir = Path(dir_str)
        
    frames_dir.mkdir(parents=True, exist_ok=True)
    return frames_dir

def get_workspace_dir() -> Path:
    """
    获取用户的统一工作区根目录（默认：我的文档/No0_AI_Workspace）。
    所有子项目、自学习资料和用户导入文件均在此处存放。
    """
    # Windows 下尝试获取 Documents 文件夹
    docs = os.environ.get("USERPROFILE")
    if docs:
        workspace_dir = Path(docs) / "Documents" / "No0_AI_Workspace"
    else:
        workspace_dir = Path.home() / "Documents" / "No0_AI_Workspace"
    
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return workspace_dir

def get_ai_index_dir() -> Path:
    """
    获取智能体专用的索引和缓存目录（位于工作区下的隐藏目录 .ai_index）。
    """
    ai_index = get_workspace_dir() / ".ai_index"
    ai_index.mkdir(parents=True, exist_ok=True)
    return ai_index
def get_resource_path(relative_path: str) -> Path:
    """
    获取静态资源的绝对路径。
    - 打包模式：从 sys._MEIPASS 中读取
    - 开发模式：从项目根目录读取
    
    Args:
        relative_path: 相对于项目根目录的路径（如 "resources/config.json"）
    """
    return get_bundle_dir() / relative_path
def get_db_path(db_name: str = "tasks.db") -> Path:
    """获取 SQLite 数据库文件的完整路径。"""
    return get_app_data_dir() / db_name


def safe_join(base_dir: Path, *segments: str) -> Path:
    """安全拼接子路径，防路径遍历（../../ 越界访问）。

    将 base_dir 与若干「用户可控」片段拼接后 resolve，并校验结果仍位于
    base_dir 内；越界时抛 ValueError。用于所有把客户端标识符（project_id /
    doc_id / book_id / card_id / template_id / context_id 等）拼入文件路径的场景。

    Windows 注意：Path.resolve() 会同时处理正反斜杠，`..` 与绝对路径段均被归一化，
    因此 `..\\..\\evil`、`C:\\...` 等均可被拦截。
    """
    base = Path(base_dir).resolve()
    target = base.joinpath(*segments).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise ValueError(
            f"非法路径标识符（越界访问被拒绝）: {segments!r}"
        ) from None
    return target