"""诊断：api_router 为何未挂载 novel-agent 路由。"""
import sys
from pathlib import Path
BASE = Path(r"C:\Users\11482\Documents\aiilie")
sys.path.insert(0, str(BASE))
import core.config_manager as cm
# 修改前
print("修改前 feature.novel_multi_agent_enable =", cm.config_manager.get("feature.novel_multi_agent_enable", False))
cm.config_manager._config.setdefault("feature", {})["novel_multi_agent_enable"] = True
print("修改后 feature.novel_multi_agent_enable =", cm.config_manager.get("feature.novel_multi_agent_enable", False))
print("config_manager id =", id(cm.config_manager))
# 导入 api_router
from api.api_router import api_router
# 从 api_router 模块里拿同一个 config_manager
import api.api_router as ar
print("api_router 内 config_manager id =", id(ar.config_manager))
print("api_router 内读取 =", ar.config_manager.get("feature.novel_multi_agent_enable", False))
def _collect_paths(routes):
    paths = []
    for r in routes:
        p = getattr(r, "path", None)
        if p:
            paths.append(p)
        sub = getattr(r, "routes", None)
        if sub:
            paths.extend(_collect_paths(sub))
    return paths
paths = _collect_paths(api_router.routes)
novel = [p for p in paths if "novel-agent" in p]
print("novel-agent 路由:", novel)
print("总路由数:", len(paths))