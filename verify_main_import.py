"""关键验证：整个应用导入链路是否正常（create_app 不启动 lifespan）。
模拟开启 feature.novel_multi_agent_enable 后启动应用（环境变量覆盖 config.yaml）。
"""
import os
import sys
from pathlib import Path
BASE = Path(r"C:\Users\11482\Documents\aiilie")
sys.path.insert(0, str(BASE))
# 在导入 main 之前开启总开关（config_manager 支持 AIILIE_ 环境变量覆盖）
os.environ["AIILIE_FEATURE_NOVEL_MULTI_AGENT_ENABLE"] = "true"
try:
    import main
    app = main.app
    print("[OK] main 模块导入成功")
    print("[OK] FastAPI app 创建成功:", app.title)
    # 验证 novel-agent 路由在 app 级可见
    # FastAPI 新版将 include_router 包装为 _IncludedRouter（懒加载），
    # 需穿透 original_router 递归收集真实路径。
    found = []
    def collect(routes):
        for r in routes:
            orig = getattr(r, "original_router", None)
            if orig is not None:
                collect(getattr(orig, "routes", []))
                continue
            p = getattr(r, "path", "")
            if p:
                if "novel-agent" in p:
                    found.append((p, getattr(r, "methods", None)))
                sub = getattr(r, "routes", None)
                if sub:
                    collect(sub)
    collect(app.routes)
    print("[OK] app 级 novel-agent 路由:")
    for p, m in found:
        print("   ", p, m)
    assert len(found) == 3, f"期望 3 条 novel-agent 路由，实际 {len(found)}"
except Exception as exc:
    import traceback
    traceback.print_exc()
    sys.exit(1)
print("\n=== 应用启动链路验证通过 ✅ ===")