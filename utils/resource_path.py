import os
import sys
from pathlib import Path

def get_resource_path(relative_path: str) -> Path:
    """
    跨平台与打包兼容的静态资源路径解析函数。
    处理 PyInstaller 打包为 EXE 后运行时释放临时目录 (sys._MEIPASS) 的路径映射。
    注意：此处仅用于读取只读文件（前端资源、内置提示词等）。
    """
    try:
        # PyInstaller 打包后的临时环境路径
        base_path = Path(sys._MEIPASS)
    except Exception:
        # 开发模式下的当前绝对路径
        base_path = Path(__file__).parent.parent.absolute()
        
    return base_path / relative_path

def get_user_data_path(relative_path: str = "") -> Path:
    r"""
    跨平台的用户数据路径解析函数，强制指向 %APPDATA%\No0_AI_V4。
    用于存放 SQLite 业务库、日志、会话缓存等可变数据，防止 EXE 升级导致数据丢失。
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata) / "No0_AI_V4"
    else:
        # 回退至用户主目录隐藏文件夹
        base = Path.home() / ".No0_AI_V4"
    
    target_path = base / relative_path if relative_path else base
    # 自动保障基础目录存在
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not target_path.suffix:  # 如果是目录，则创建
        target_path.mkdir(parents=True, exist_ok=True)
        
    return target_path
