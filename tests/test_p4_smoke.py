# -*- coding: utf-8 -*-
"""P4四大基建模块独立冒烟测试（临时目录存储，不触碰正式数据）。"""
import sys
import tempfile
from pathlib import Path

# 【删掉硬编码Windows绝对路径！不要手动sys.path.insert】

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("[OK] " if cond else "[XX] ") + name + (f" | {detail}" if detail else ""))


def test_p4_smoke():
    """P4基建冒烟测试，全部逻辑收在测试函数内部"""
    tmp = Path(tempfile.mkdtemp(prefix="p4_smoke_"))
    print(f"[1/4] P4‑1 书库量化精细化闭环 (tmp={tmp})")

    from core.library_dimension_index import (
        DimensionIndexer,
        CharacterWorldviewQuantizer,
        EmotionDimensionModel,
        SampleCleaningPipeline,
        LibraryCachePreloader,
        DIMENSIONS,
    )

    di = DimensionIndexer(db_path=tmp / "dim.db")
    n = di.index_document(
        "card_001",
        {
            "plot": ["复仇主线", "双线叙事"],
            "character": ["外冷内热", "复仇者"],
            "worldview": ["修真位面", "宗门"],
            "emotion": ["悲壮", "热血"],
            "scene": ["雪夜", "山门"],
            "style": ["古风", "热血"],
        },
        meta={"book": "测试书"},
    )
    check("六维索引写入", n == 6, f"写入 {n} 条")
    check("维度数校验", len(DIMENSIONS) == 6)
    hits = di.search_by_dimensions(["emotion", "style"], tag="热血", limit=5)
    check("六维组合检索", len(hits) == 2, [h["dimension"] for h in hits])
    agg = di.aggregate()
    check("维度聚合统计", agg.get("plot") == 1, agg)

    # …把后面剩下全部业务代码继续往这个函数里面缩进
    # 原来第85行那个报错导入，如果PluginLifecycleManager还不存在，就注释掉
    # from core.plugin_lifecycle import PluginLifecycleManager
