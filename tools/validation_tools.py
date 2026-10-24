"""Explicit read-only verification; never execute ProposedFix steps."""

from models.validation import ValidationCheck, ValidationResult
from tools.api_client import MockApiClient
from tools.schema_validator import validate_schema


class ValidationTools:
    def __init__(self, *, client: MockApiClient, operation_id: str, response_schema):
        if operation_id not in client.operations:
            raise ValueError("Unknown validation operation")
        self.client, self.operation_id, self.response_schema = client, operation_id, response_schema

    def __call__(self, _state) -> ValidationResult:
        # State may contain malicious URLs, headers, or shell steps. None are used.
        result = self.client.execute(self.operation_id)
        return validation_from_result(result, self.response_schema)


def validation_from_result(result, response_schema):
    if result.error or result.status_code is None:
        return ValidationResult(status="inconclusive", issues=[
                "Configured read-only validation could not obtain a safe response"])
    if not result.ok:
        return ValidationResult(status="failed", checks=[ValidationCheck(
                name="mock_response", status="failed", detail="Configured mock request returned a failure status")])
    contract = validate_schema(result.body, response_schema)
    return ValidationResult(status="passed" if contract.valid else "failed", checks=[ValidationCheck(
            name="mock_response_contract", status="passed" if contract.valid else "failed",
            detail="Configured mock response satisfies its contract" if contract.valid else "Mock response violates its contract")],
            issues=contract.issues)
