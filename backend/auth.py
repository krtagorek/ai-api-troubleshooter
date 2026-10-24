"""Trusted authentication adapters; demo identity is server-selected and development-only."""
import os
import secrets
from typing import Protocol
from uuid import UUID
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from registry.authorization import Principal

bearer = HTTPBearer(auto_error=False)


class Authenticator(Protocol):
    async def authenticate(self, token: str) -> Principal | None: ...


class DemoAuthenticator:
    def __init__(self, subject, token):
        if len(token) < 32:
            raise ValueError("DEMO_AUTH_TOKEN must contain at least 32 characters")
        self.subject, self._token = UUID(str(subject)), token

    async def authenticate(self, token):
        return Principal(self.subject) if secrets.compare_digest(token.encode(), self._token.encode()) else None


def authentication_from_env(*, keys=None, revocations=None):
    if os.getenv("DEMO_AUTH_ENABLED", "false").lower() != "true":
        issuer,audience=os.getenv('IDENTITY_ISSUER'),os.getenv('IDENTITY_AUDIENCE')
        if issuer or audience:
            if not issuer or not audience or keys is None or revocations is None:
                raise ValueError('Configure issuer, audience and trusted key/revocation providers')
            from diagnostic_contracts.identity import JWTAuthenticator
            return JWTAuthenticator(issuer=issuer,audience=audience,keys=keys,revocations=revocations)
        return None
    if os.getenv("APP_ENV", "production") != "development":
        raise ValueError("Demo authentication requires APP_ENV=development")
    try:
        return DemoAuthenticator(os.environ["DEMO_SUBJECT_ID"], os.environ["DEMO_AUTH_TOKEN"])
    except Exception:
        raise ValueError("Configure a UUID DEMO_SUBJECT_ID and a DEMO_AUTH_TOKEN of at least 32 characters") from None


async def identity(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    provider = request.app.state.authenticator
    principal = None
    if credentials and provider:
        try:
            principal = await provider.authenticate(credentials.credentials)
        except Exception:
            principal = None
    if not isinstance(principal, Principal):
        raise HTTPException(401, "Valid bearer authentication required", headers={"WWW-Authenticate": "Bearer"})
    return principal
