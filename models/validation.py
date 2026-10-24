"""Validation describes checks, not execution of fixes."""

from typing import Literal

from pydantic import Field, model_validator

from models.incident import DomainModel


class ValidationCheck(DomainModel):
    name: str = Field(min_length=1)
    status: Literal["passed", "failed", "not_run", "inconclusive"]
    detail: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class ValidationResult(DomainModel):
    status: Literal["not_run", "NOT_TESTED", "passed", "failed", "inconclusive"] = "not_run"
    checks: list[ValidationCheck] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    retry_recommended: bool = False

    @model_validator(mode="after")
    def passed_requires_checks(self):
        if self.status == "passed" and (
            not self.checks or any(check.status != "passed" for check in self.checks)
        ):
            raise ValueError("Passed validation requires completed, passing checks")
        return self
