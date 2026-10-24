"""Validate response contracts supplied by application code, never by an LLM."""

from pydantic import TypeAdapter, ValidationError, Field

from models.incident import DomainModel


class SchemaValidationResult(DomainModel):
    valid: bool
    issues: list[str] = Field(default_factory=list)


def validate_schema(payload, schema) -> SchemaValidationResult:
    try:
        TypeAdapter(schema).validate_python(payload, strict=True)
        return SchemaValidationResult(valid=True)
    except ValidationError as exc:
        # Do not include input values or attacker-controlled dictionary keys.
        return SchemaValidationResult(valid=False, issues=[
            "Response contract violation: " + error["type"] for error in exc.errors(include_input=False)])
    except Exception:
        return SchemaValidationResult(valid=False, issues=["Response contract could not be validated"])
