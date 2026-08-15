"""语法检查：验证第十部分全部新增/修改文件可正常编译导入。"""
import sys
import py_compile
from pathlib import Path
BASE = Path(r"C:\Users\11482\Documents\aiilie")
files = [
    "models/novel_agent.py",
    "models/reflection.py",
    "models/__init__.py",
    "services/novel_supervisor.py",
    "services/novel_agent_audit_store.py",
    "services/novel_agent_skill_store.py",
    "services/novel_agent_learning_loop.py",
    "extractors/novel_agent_extractors.py",
    "api/novel_agent.py",
    "api/api_router.py",
    "core/bootstrap.py",
    "services/global_router.py",
]
ok = True
for f in files:
    p = BASE / f
    try:
        py_compile.compile(str(p), doraise=True)
        print(f"[OK] {f}")
    except Exception as e:
        ok = False
        print(f"[FAIL] {f}: {e}")
print("\n=== 结果:", "全部通过 ✓" if ok else "存在失败 ✗")
sys.exit(0 if ok else 1)