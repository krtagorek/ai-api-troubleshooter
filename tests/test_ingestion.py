"""Synthetic Ed25519 keys only. No network or provider calls."""
import base64
import asyncio
import functools

def run_async(fn):
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):return asyncio.run(fn(*args,**kwargs))
    return wrapped

from dataclasses import replace
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from diagnostic_contracts.ingestion import Payload,Envelope,ProducerKey,IngestionService,digest
from diagnostic_contracts.service import DiagnosticDenied

class Keys:
    def __init__(self,key):self.key=key
    async def resolve(self,kid):return self.key if kid=='test-key' else None

def sample():
    now=datetime.now(timezone.utc)
    snapshot={'incident_id':'synthetic-only','logs':[{'epoch':4}]}
    p=Payload(version='signed-observations-1',envelope_id=uuid4(),grant_id=uuid4(),tenant_id=uuid4(),api_id=uuid4(),producer_id=uuid4(),contract_id=uuid4(),contract_version=1,contract_digest='a'*64,endpoint_revision=1,binding_revision=1,source='service_logs',resource='test',correlation_id='request-1',captured_at=now,snapshot=snapshot,snapshot_digest=digest(snapshot))
    private=Ed25519PrivateKey.generate()
    key=ProducerKey(private.public_key(),p.producer_id,p.tenant_id,p.api_id,now-timedelta(seconds=5),now+timedelta(seconds=90),now+timedelta(seconds=20))
    return p,private,Keys(key)

def sign(p,private):
    e=Envelope(algorithm='Ed25519',key_id='test-key',payload=p,signature='A'*86+'==')
    return e.model_copy(update={'signature':base64.urlsafe_b64encode(private.sign(e.signed_bytes())).decode()})

@run_async
async def test_valid_signature_and_canonical_serialization():
    p,k,keys=sample();svc=IngestionService(None,None,keys=keys)
    assert len(await svc._key(sign(p,k)))==64
    assert digest({'b':2,'a':1})==digest({'a':1,'b':2})

@run_async
@pytest.mark.parametrize('failure',['tamper','signature','unknown','revoked','rotated','expired','future','outage','tenant','endpoint','stale_keys'])
async def test_signature_fail_closed(failure):
    p,k,keys=sample();e=sign(p,k)
    if failure=='tamper':e=e.model_copy(update={'payload':p.model_copy(update={'resource':'changed'})})
    if failure=='signature':e=e.model_copy(update={'signature':'A'*86+'=='})
    if failure=='unknown':e=e.model_copy(update={'key_id':'unknown'})
    if failure=='revoked':keys.key=replace(keys.key,revoked=True)
    if failure=='rotated':keys.key=replace(keys.key,public_key=Ed25519PrivateKey.generate().public_key())
    if failure=='expired':e=sign(p.model_copy(update={'captured_at':p.captured_at-timedelta(seconds=121)}),k)
    if failure=='future':e=sign(p.model_copy(update={'captured_at':p.captured_at+timedelta(minutes=1)}),k)
    if failure=='outage':keys=None
    if failure=='tenant':keys.key=replace(keys.key,tenant_id=uuid4())
    if failure=='endpoint':keys.key=replace(keys.key,api_id=uuid4())
    if failure=='stale_keys':keys.key=replace(keys.key,checked_until=p.captured_at)
    with pytest.raises(DiagnosticDenied):await IngestionService(None,None,keys=keys)._key(e)

@pytest.mark.parametrize('change',[{'algorithm':'none'},{'key_id':'https://attacker.invalid'},{'signature':''}])
def test_schema_rejects_unsafe_envelope(change):
    p,k,_=sample();d=sign(p,k).model_dump();d.update(change)
    with pytest.raises(ValueError):Envelope.model_validate(d)

@pytest.mark.parametrize('snapshot',[{'password':'private'},{'root_cause':'label'},{'value':float('nan')}])
def test_reject_secrets_labels_and_noncanonical_snapshot(snapshot):
    p,_,_=sample()
    with pytest.raises(ValueError):Payload.model_validate({**p.model_dump(),'snapshot':snapshot})

@run_async
async def test_rotated_keys_with_distinct_ids_and_expiry():
    p,private,keys=sample()
    e=sign(p,private)
    # Rotation can retain the old, explicitly unrevoked key. Changing the key
    # material for its identifier fails, covered separately above.
    assert await IngestionService(None,None,keys=keys)._key(e)
    keys.key=replace(keys.key,expires_at=p.captured_at)
    with pytest.raises(DiagnosticDenied):await IngestionService(None,None,keys=keys)._key(e)


def test_nested_construct_bypass_and_snapshot_metadata_secrets():
    p,private,_=sample()
    invalid=p.model_copy(update={'snapshot':{'password':'do-not-store'}})
    with pytest.raises(ValueError):Envelope.model_validate(sign(invalid,private).model_dump())
    with pytest.raises(ValueError):Payload.model_validate({**p.model_dump(),'correlation_id':'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abcdefghijklmnopqrstuvwxyz'})

@run_async
async def test_missing_identity_boundary_fails_closed_before_storage():
    p,k,keys=sample()
    service=IngestionService(None,None,keys=keys)
    with pytest.raises(DiagnosticDenied):await service.ingest(None,sign(p,k))
    with pytest.raises(DiagnosticDenied):await service.authorize_producer(None,p)
