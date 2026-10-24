"""Public response contracts deliberately exclude diagnostic graph internals."""
from uuid import UUID
from pydantic import BaseModel
from models.incident import Incident
from registry.models import ApiRegistration


class InvestigationSummary(BaseModel):
    investigation_id: UUID
    thread_id: UUID
    status: str


class IncidentDetail(BaseModel):
    incident: Incident
    investigations: list[InvestigationSummary]


class IncidentPage(BaseModel):
    items: list[Incident]
    limit: int
    offset: int


class RegistrationResponse(BaseModel):
    registration: ApiRegistration
    verification_challenge: str
