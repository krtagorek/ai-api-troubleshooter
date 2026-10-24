"""Permit capabilities and missing provider checks; no paid transport."""
import asyncio
from uuid import uuid4
import pytest
from pydantic import SecretStr
from diagnostic_contracts.permits import Permit,PermitService
from diagnostic_contracts.service import DiagnosticDenied
from tests.test_diagnostic_identity import configured,token


def test_capability_is_not_exposed_by_repr():
    p=Permit(uuid4(),SecretStr('never-log-this-capability'))
    assert 'never-log-this' not in repr(p)


def test_fresh_identity_lease_requires_revocation_and_valid_keys():
    async def run():
        auth,key,keys,rev=configured();actor=await auth.authenticate(token(key))
        assert await auth.authorization_expiry(actor)>auth.clock()
        rev.allowed=False;assert await auth.authorization_expiry(actor) is None
        rev.allowed=True;keys.stale=True
        assert await auth.authorization_expiry(actor) is None
    asyncio.run(run())


def test_identity_rotation_during_fresh_lease_check_is_rejected():
    async def run():
        from diagnostic_contracts.identity import KeySnapshot
        from datetime import timedelta
        auth,key,keys,rev=configured();actor=await auth.authenticate(token(key))
        original=keys.snapshot;calls=[]
        async def rotating():
            calls.append(1)
            return await original() if len(calls)==1 else KeySnapshot({},auth.clock()+timedelta(seconds=5))
        keys.snapshot=rotating
        assert await auth.authorization_expiry(actor) is None
    asyncio.run(run())
