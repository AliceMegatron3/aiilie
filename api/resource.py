"""
api/resource.py — 资源路由聚合器
=================================
补丁E 阶段的占位实现已废弃：resource-stats / gc-run / gc-report
三个路由的正规实现已迁移至 api/system.py（core/gc_manager.py + 
SystemMonitor 空闲判定 VACUUM），此处仅保留空路由容器，
避免重复挂载产生 Duplicate Operation ID。
"""
from fastapi import APIRouter, Depends
from api.deps import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])
