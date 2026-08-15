import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from core.path_resolver import get_log_dir
import os
def setup_global_logger():
    # 确保 logs 目录存在
    log_dir = get_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = log_dir / "system.log"
    
    # 获取根 Logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # 清理已有的 handlers，防止重复添加
    if logger.hasHandlers():
        logger.handlers.clear()
        
    # 定义统一的格式
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    
    # 1. 终端输出
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 2. 文件按 50MB 轮转，最多保留 5 份历史
    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=50 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logging.info("全局统一日志系统 (Centralized Logging) 初始化完成。")