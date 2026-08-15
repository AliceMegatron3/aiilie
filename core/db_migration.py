import aiosqlite
import logging
from pathlib import Path
from core.path_resolver import get_db_path
logger = logging.getLogger(__name__)
# 定义目标 Schema 版本号
TARGET_SCHEMA_VERSION = 1
async def init_and_migrate_db():
    db_path = get_db_path("database.db")
    # 确保 data 目录存在
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"正在巡检数据库: {db_path}")
    
    async with aiosqlite.connect(db_path) as db:
        # 1. 确保系统版本表存在
        await db.execute("""
            CREATE TABLE IF NOT EXISTS _schema_version (
                version INTEGER PRIMARY KEY
            )
        """)
        
        # 获取当前版本
        async with db.execute("SELECT MAX(version) FROM _schema_version") as cursor:
            row = await cursor.fetchone()
            current_version = row[0] if row and row[0] is not None else 0
            
        logger.info(f"当前数据库版本: v{current_version}, 目标版本: v{TARGET_SCHEMA_VERSION}")
        
        if current_version < TARGET_SCHEMA_VERSION:
            await _run_migrations(db, current_version, TARGET_SCHEMA_VERSION)
            await db.execute("INSERT INTO _schema_version (version) VALUES (?)", (TARGET_SCHEMA_VERSION,))
            await db.commit()
            logger.info("数据库迁移完成。")
        else:
            logger.info("数据库已是最新版本，无需迁移。")
async def _run_migrations(db: aiosqlite.Connection, current: int, target: int):
    # 此处放置各批次的表结构初始化与未来的跨版本增量 SQL
    if current < 1:
        logger.info("执行 v0 -> v1 全量建表脚本...")
        # 批次1: 任务队列表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS system_tasks (
                task_id TEXT PRIMARY KEY,
                batch_id TEXT,
                status TEXT,
                created_at DATETIME,
                updated_at DATETIME
            )
        """)
        # 批次2: 知识库卡片表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_cards (
                id TEXT PRIMARY KEY,
                book_id TEXT,
                content TEXT,
                vector BLOB
            )
        """)
        # 批次3: 项目表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT
            )
        """)
        # 批次4: 规则引擎表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS optimization_rules (
                id TEXT PRIMARY KEY,
                rule_type TEXT,
                condition TEXT,
                action TEXT,
                confidence REAL
            )
        """)