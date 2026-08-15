"""诊断：遍历 _IncludedRouter.original_router 找 novel-agent 路由。"""
import sys
from pathlib import Path
BASE = Path(r"C:\Users\11482\Documents\aiilie")
sys.path.insert(0, str(BASE))
import core.config_manager as cm
cm.config_manager._config.setdefault("feature", {})["novel_multi_agent_enable"] = True
from api.api_router import api_router
def collect(routes, depth=0):
    paths = []
    for r in routes:
        # _IncludedRouter 包装
        orig = getattr(r, "original_router", None)
        if orig is not None:
            sub = getattr(orig, "routes", [])
            paths.extend(collect(sub, depth + 1))
            continue
        p = getattr(r, "path", None)
        if p:
            paths.append(p)
        sub = getattr(r, "routes", None)
        if sub:
            paths.extend(collect(sub, depth + 1))
    return paths
paths = collect(api_router.routes)
print("总路由数:", len(paths))
novel = [p for p in paths if "novel-agent" in p]
print("novel-agent 路由:", novel)
print("\n全部路由预览:")
for p in paths:
    print(" ", p)