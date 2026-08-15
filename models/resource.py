from pydantic import BaseModel
from typing import Dict

class ResourceStats(BaseModel):
    memory_usage_percent: float
    disk_free_gb: float
    directory_usage_mb: Dict[str, float]
    cache_object_count: int

class GcTaskReport(BaseModel):
    task_id: str
    status: str
    files_cleaned: int
    bytes_freed: float
    vacuum_executed: bool
    message: str
