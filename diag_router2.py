"""诊断：打印 api_router 路由结构。"""
import sys
from pathlib import Path
BASE = Path(r"C:\Users\11482\Documents\aiilie")
sys.path.insert(0, str(BASE))
from api.api_router import api_router
print("顶层 routes 数量:", len(api_router.routes))
for i, r in enumerate(api_router.routes[:15]):
    print(f"[{i}] type={type(r).__name__}, attrs={[a for a in dir(r) if not a.startswith('_')][:20]}")