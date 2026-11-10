from datetime import datetime, timezone
from pydantic import BaseModel, Field

class SkillPotential(BaseModel):
    skill_id: str
    estimated_level: float = Field(..., ge=1.0, le=10.0, description="Robocze oszacowanie osiągalnego poziomu (1-10)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Pewność oszacowania potencjału")
    evidence_count: int = Field(default=0, ge=0, description="Liczba obserwacji")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
