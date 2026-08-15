"""
core/logging_config.py — 统一日志配置
======================================
配置结构化日志，输出到控制台和文件。
日志文件存储在 %APPDATA%/No0_AI_V4/data/logs/ 目录下。
"""
from __future__ import annotations
import logging
import logging.handlers
import sys
from pathlib import Path
from core.path_resolver import get_log_dir
def setup_logging(level: int = logging.INFO) -> None:
    """初始化全局日志配置。"""
    log_dir: Path = get_log_dir()
    log_file = log_dir / "engine.log"
    # 日志格式
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    # 根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    # 避免重复添加 handler
    if root_logger.handlers:
        return
    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root_logger.addHandler(console_handler)
    # 文件 handler（使用 RotatingFileHandler 实现日志轮转）
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root_logger.addHandler(file_handler)
    logging.info("日志系统初始化完成，日志文件: %s", log_file)