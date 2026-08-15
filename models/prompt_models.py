from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone
class PromptTemplate(BaseModel):
    template_id: str
    name: str
    category: str
    version: str = "1.0.0"
    inherit_template_id: Optional[str] = None
    model_variant: Optional[str] = None
    template_text: str
    variables: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    is_builtin: bool = False
    create_time: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    update_time: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
class TemplateRenderRequest(BaseModel):
    template_id: str
    variables: dict