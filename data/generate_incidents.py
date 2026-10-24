"""Generate synthetic benchmarks and label-free inference inputs.

Run: python -m data.generate_incidents
"""

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SEED = 314159
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "incidents"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RequestMetadata(StrictModel):
    request_id: str
    timestamp: str
    environment: Literal["synthetic"]
    attempt: int = Field(ge=1)
    timeout_ms: int = Field(gt=0)
    evidence_quality: Literal["complete", "missing", "ambiguous"]
    headers: dict[str, str]


class LogEntry(StrictModel):
    timestamp: str
    level: Literal["INFO", "WARNING", "ERROR"]
    event: str
    service: str
    correlation_id: str
    message: str
    fields: dict[str, str | int]


class InferenceIncident(StrictModel):
    incident_id: str
    service: str
    endpoint: str
    http_method: Literal["GET", "POST"]
    status_code: Literal[200, 401, 403, 422, 429, 500, 503, 504]
    request_metadata: RequestMetadata
    response_body: dict
    logs: list[LogEntry]
    expected_behavior: str
    actual_behavior: str
    severity: Literal["low", "medium", "high", "critical"]


class Incident(InferenceIncident):
    root_cause: str
    expected_resolution: str


# Status -> (cause, resolution, diagnostic observation). All content is synthetic.
CAUSES = {
    401: [
        ("Access token expired before the request", "Refresh the token and retry once with the new token", "Token expiration is 90 seconds before server time"),
        ("Proxy stripped the Authorization header", "Forward Authorization through the proxy and verify header presence", "Gateway received authorization_present=true; upstream observed false"),
        ("Token audience targets a different API", "Request a token for the intended API audience", "Token audience is billing-api; expected inventory-api"),
    ],
    403: [
        ("Token lacks orders:write scope", "Grant orders:write through the approved permission flow and reissue the token", "Required scope orders:write is absent"),
        ("Caller tenant does not own the resource", "Use the correct tenant and a resource owned by that tenant", "Caller tenant demo-a differs from resource tenant demo-b"),
        ("Source IP is blocked by the API allowlist", "Add the approved egress IP to the allowlist", "Policy decision denies synthetic source 192.0.2.10"),
    ],
    422: [
        ("Quantity was serialized as a string", "Send quantity as a positive JSON integer", "Validation failed at quantity: expected integer, received string"),
        ("Required customer_id was omitted", "Include a valid customer_id in the order payload", "Validation failed at customer_id: field required"),
        ("Order currency is outside the supported enum", "Use a supported currency such as USD", "Validation failed at currency: supported values USD and EUR"),
    ],
    429: [
        ("Client exceeded the per-second burst limit", "Throttle requests and honor Retry-After with jittered backoff", "Observed 120 requests in a one-second window; limit is 100"),
        ("Account exhausted its daily request quota", "Wait for quota reset or request an approved quota increase", "Daily quota remaining=0; reset is at the next UTC midnight"),
        ("Immediate retries amplified throttling", "Bound retries and use exponential backoff honoring Retry-After", "Five retries arrived within 40 milliseconds after Retry-After=30"),
    ],
    504: [
        ("Gateway timeout is shorter than normal upstream latency", "Align timeout budgets with the upstream latency objective", "Gateway budget=1000ms; upstream completed in 1400ms"),
        ("Upstream database query lacks an index", "Add the appropriate query index and verify query latency", "Query plan uses full scan; duration=12000ms"),
        ("Upstream connection pool is exhausted", "Bound concurrency and size the pool after investigating leaked connections", "Pool active=20 max=20; acquire wait exceeded 5000ms"),
    ],
    500: [
        ("Server dereferenced a null customer profile", "Guard missing profiles and deploy a regression-tested fix", "Unhandled AttributeError in customer profile serialization"),
        ("Server database migration was not applied", "Apply the required migration and verify schema compatibility", "Database error: column orders.external_reference does not exist"),
        ("Server cannot load its signing configuration", "Restore the signing configuration and validate it on startup", "Configuration lookup failed for signing key reference demo-key"),
    ],
    503: [
        ("Payment dependency circuit breaker is open", "Restore dependency health and allow a controlled circuit probe", "Payment circuit state=open after five connection failures"),
        ("Service discovery points to a retired dependency", "Update the dependency service discovery record", "Discovery returned retired host payments-old.example.invalid"),
        ("Dependency TLS certificate expired", "Renew the dependency certificate and verify the certificate chain", "TLS handshake failed: synthetic dependency certificate expired"),
    ],
    200: [
        ("API version changed quantity from integer to string", "Pin a compatible API version or update and validate the consumer contract", "Contract check: data.quantity expected integer, received string"),
        ("Gateway returned a login document instead of JSON", "Correct gateway authentication routing and validate Content-Type", "Content-Type=text/html; body begins with a demo login document"),
        ("Deployed serializer renamed the customer identifier", "Restore the id field or coordinate a versioned contract change", "Contract check: data.id missing; data.customer_id present"),
    ],
}


def inference_view(record: Incident) -> InferenceIncident:
    """Allowlist inference fields; never pass a labeled Incident to an agent."""
    return InferenceIncident.model_validate({
        field: getattr(record, field) for field in InferenceIncident.model_fields
    })


def generate(seed: int = SEED) -> dict[str, list[Incident]]:
    rng = random.Random(seed)
    splits: dict[str, list[Incident]] = {"development": [], "test": []}
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for group, (status, causes) in enumerate(CAUSES.items()):
        for index in range(15):
            cause_index = index % 3
            cause, resolution, observation = causes[cause_index]
            number = group * 15 + index + 1
            timestamp = (base + timedelta(seconds=number * 73)).isoformat()
            correlation = f"synthetic-request-{number:04d}"
            quality = ("complete", "missing", "ambiguous")[index // 5]
            if status in (403, 422, 429, 500, 503):
                service, endpoint, method = "orders-api", "/orders", "POST"
            elif status == 200 and cause_index == 2:
                service, endpoint, method = "customers-api", "/customers/1", "GET"
            else:
                service, endpoint, method = "inventory-api", "/inventory/1", "GET"
            response: dict = {"error": {"code": f"HTTP_{status}", "message": "Request could not be completed"}}
            if status == 200:
                response = [
                    {"data": {"id": "1", "quantity": "12"}},
                    {"content_type": "text/html", "raw_body": "<html>Demo login</html>"},
                    {"data": {"customer_id": "1", "name": "Demo Customer"}},
                ][cause_index]
            response = {**response, "request_id": correlation, "timestamp": timestamp}
            logs = [LogEntry(
                timestamp=timestamp, level="INFO", event="request_started",
                service=service, correlation_id=correlation,
                message="Outbound synthetic API request started",
                fields={"method": method, "route": endpoint, "attempt": 1},
            )]
            if quality == "complete":
                logs.append(LogEntry(
                    timestamp=timestamp, level="ERROR", event="diagnostic_observation",
                    service=service, correlation_id=correlation, message=observation,
                    fields={"status_code": status},
                ))
            elif quality == "ambiguous":
                logs.append(LogEntry(
                    timestamp=timestamp, level="WARNING", event="telemetry_incomplete",
                    service=service, correlation_id=correlation,
                    message="Gateway and upstream telemetry disagree; upstream trace is unavailable",
                    fields={"gateway_status": status, "upstream_status": "unknown"},
                ))
            # Missing evidence intentionally omits the diagnostic event entirely.
            logs.append(LogEntry(
                timestamp=timestamp, level="ERROR", event="request_completed",
                service=service, correlation_id=correlation,
                message="Response contract failed" if status == 200 else "API request failed",
                fields={"status_code": status, "duration_ms":
                        (1000 if cause_index == 0 else 5000) if status == 504
                        else rng.randint(10, 900)},
            ))
            actual = "HTTP 200 received, but the response violates the expected JSON contract" if status == 200 else f"Request failed with HTTP {status}"
            record = Incident(
                incident_id=f"INC-{number:04d}", service=service, endpoint=endpoint,
                http_method=method, status_code=status,
                request_metadata=RequestMetadata(
                    request_id=correlation, timestamp=timestamp, environment="synthetic",
                    attempt=1, timeout_ms=1000 if status == 504 and cause_index == 0 else 5000,
                    evidence_quality=quality,
                    headers={"Accept": "application/json", "X-Request-ID": correlation},
                ), response_body=response, logs=logs,
                expected_behavior=("Return HTTP 200 with JSON data containing string id and name and integer quantity"
                    if service == "inventory-api" else
                    "Return HTTP 200 with JSON data containing string id and name"
                    if service == "customers-api" else
                    "Accept a valid order and return HTTP 200 or 201 with a JSON order identifier"),
                actual_behavior=actual, root_cause=cause, expected_resolution=resolution,
                severity="critical" if status in (500, 503) else "high" if status in (504, 200) else "medium",
            )
            # 10 development + 5 test per status; both splits cover all causes.
            # Rotate the held-out cause across cycles to avoid a cause-only split.
            split = "test" if index % 3 == (index // 3) % 3 else "development"
            splits[split].append(record)
    for records in splits.values():
        rng.shuffle(records)
    return splits


def write_dataset(output: Path = DEFAULT_OUTPUT, seed: int = SEED) -> None:
    for split, records in generate(seed).items():
        for directory, views in (("labels", records),
                                 ("inference", [inference_view(r) for r in records])):
            destination = output / directory / f"{split}.jsonl"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("".join(
                json.dumps(view.model_dump(), sort_keys=True) + "\n" for view in views
            ), encoding="utf-8")


def load_inference(split: Literal["development", "test"],
                   output: Path = DEFAULT_OUTPUT) -> list[InferenceIncident]:
    """Load only the inference directory and reject accidental labeled records."""
    if split not in ("development", "test"):
        raise ValueError("Unknown dataset split")
    return [InferenceIncident.model_validate_json(line) for line in
            (output / "inference" / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    write_dataset(args.output_dir, args.seed)
    print(f"Generated 80 development and 40 test incidents in {args.output_dir}")


if __name__ == "__main__":
    main()
