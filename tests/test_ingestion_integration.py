"""Disposable PostgreSQL + synthetic identity, proofs, and signing keys."""
import asyncio
from datetime import timedelta
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text,select
from tests.test_postgres_integration import settings,migrate
from tests.test_diagnostic_authority_integration import setup,staged
from tests.test_ingestion import sample,sign
from persistence.runtime import open_persistence
from persistence.ingestion import IngestionEnvelope
from diagnostic_contracts.ingestion import IngestionService,Payload
from diagnostic_contracts.service import DiagnosticDenied
from persistence.database import PersistenceError
pytestmark=pytest.mark.integration

async def fixture(rt):
    svc,authority,auth,proof,t,o,a,r,ep,rev=await setup(rt)
    v=await staged(svc,t,o,a,r,ep)
    p,key,keys=sample()
    from dataclasses import replace
    p=Payload.model_validate({**p.model_dump(),'tenant_id':t,'api_id':ep.api_id,'producer_id':o.subject_id,'contract_id':UUID(v['contract_id']),'contract_digest':v['digest'],'endpoint_revision':ep.revision})
    keys.key=replace(keys.key,tenant_id=t,api_id=ep.api_id,producer_id=o.subject_id)
    ingestion=IngestionService(rt.database,authority,keys=keys)
    await ingestion.authorize_producer(a,p)
    return ingestion,p,key,keys,o,a,authority


def test_durable_signature_reverification_replay_and_immutability(settings):
    async def run():
        async with open_persistence(settings) as rt:
            svc,p,key,keys,o,a,authority=await fixture(rt)
            ident=await svc.ingest(o,sign(p,key))
            args=dict(tenant_id=p.tenant_id,api_id=p.api_id,contract_id=p.contract_id,contract_version=1,snapshot_digest=p.snapshot_digest)
            assert (await svc.verify_provenance(o,ident,**args))['diagnostic_truth'] is False
            with pytest.raises(DiagnosticDenied):await svc.ingest(o,sign(p,key))
            with pytest.raises(DiagnosticDenied):await svc.verify_provenance(o,ident,**{**args,'tenant_id':uuid4()})
            for sql in ('UPDATE app.diagnostic_ingestion_envelopes SET signature_valid=false','DELETE FROM app.diagnostic_ingestion_envelopes','TRUNCATE app.diagnostic_ingestion_envelopes'):
                with pytest.raises(PersistenceError):
                    async with rt.database.transaction() as s:await s.execute(text(sql))
            await svc.revoke_producer(a,p.grant_id)
            with pytest.raises(DiagnosticDenied):await svc.verify_provenance(o,ident,**args)
        await asyncio.to_thread(migrate,settings,'check')
    asyncio.run(run())


def test_concurrent_ingestion_conflicts_chronology_and_outage(settings):
    async def run():
        async with open_persistence(settings) as rt:
            svc,p,key,keys,o,a,au=await fixture(rt)
            results=await asyncio.gather(svc.ingest(o,sign(p,key)),svc.ingest(o,sign(p,key)),return_exceptions=True)
            assert sum(isinstance(x,UUID) for x in results)==1
            assert sum(isinstance(x,DiagnosticDenied) for x in results)==1
            for change in ({'envelope_id':uuid4()}, {'envelope_id':uuid4(),'correlation_id':'other','captured_at':p.captured_at-timedelta(seconds=1)}):
                with pytest.raises(DiagnosticDenied):await svc.ingest(o,sign(p.model_copy(update=change),key))
            svc.keys=None
            with pytest.raises(DiagnosticDenied):await svc.ingest(o,sign(p.model_copy(update={'envelope_id':uuid4(),'correlation_id':'third'}),key))
            async with rt.database.transaction() as s:
                assert len((await s.scalars(select(IngestionEnvelope))).all())==1
    asyncio.run(run())


def test_binding_transfer_revision_and_missing_authorization(settings):
    async def run():
        async with open_persistence(settings) as rt:
            svc,p,key,keys,o,a,au=await fixture(rt)
            with pytest.raises(DiagnosticDenied):await svc.authorize_producer(o,p.model_copy(update={'grant_id':uuid4()}))
            for change in ({'contract_version':2},{'endpoint_revision':p.endpoint_revision+1},{'tenant_id':uuid4()},{'api_id':uuid4()},{'grant_id':uuid4()}):
                with pytest.raises((DiagnosticDenied,ValueError)):await svc.ingest(o,sign(p.model_copy(update=change),key))
            await au.bind_endpoint(a,uuid4(),p.api_id,o.subject_id,expected_revision=1,endpoint_revision=p.endpoint_revision)
            with pytest.raises(DiagnosticDenied):await svc.ingest(o,sign(p,key))
    asyncio.run(run())


def test_revoked_key_expired_grant_and_contract_revocation(settings):
    async def run():
        from dataclasses import replace
        from persistence.ingestion import IngestionGrant
        from persistence.diagnostic_contracts import DiagnosticVersionRow
        async with open_persistence(settings) as rt:
            svc,p,key,keys,o,a,au=await fixture(rt)
            ident=await svc.ingest(o,sign(p,key))
            args=dict(tenant_id=p.tenant_id,api_id=p.api_id,contract_id=p.contract_id,contract_version=1,snapshot_digest=p.snapshot_digest)
            original=keys.key
            keys.key=replace(original,revoked=True)
            with pytest.raises(DiagnosticDenied):await svc.verify_provenance(o,ident,**args)
            keys.key=original
            svc.clock=lambda:p.captured_at+timedelta(seconds=121)
            with pytest.raises(DiagnosticDenied):await svc.verify_provenance(o,ident,**args)
            svc.clock=au.clock
            # Real lifecycle revocation; no direct mutation of immutable provenance.
            from diagnostic_contracts.service import DiagnosticRegistry
            registry=DiagnosticRegistry(rt.database,tenants=au,admins=au.global_admins,ownership=au,security=au)
            async with rt.database.transaction() as s:v=await s.get(DiagnosticVersionRow,(p.contract_id,1));revision=v.revision
            # Service CAS uses parent lifecycle revision; obtain it from history.
            from persistence.diagnostic_contracts import DiagnosticContractRow
            async with rt.database.transaction() as s:parent=await s.get(DiagnosticContractRow,p.contract_id);revision=parent.revision
            await registry.transition(a,p.tenant_id,p.contract_id,1,'revoked',revision)
            with pytest.raises(DiagnosticDenied):await svc.verify_provenance(o,ident,**args)
    asyncio.run(run())


def test_provenance_binding_and_audit_consistency(settings):
    async def run():
        from persistence.diagnostic_authority import AuthorityEventRow
        async with open_persistence(settings) as rt:
            svc,p,key,keys,o,a,au=await fixture(rt)
            ident=await svc.ingest(o,sign(p,key))
            async with rt.database.transaction() as s:
                row=await s.get(IngestionEnvelope,ident)
                event=await s.scalar(select(AuthorityEventRow).where(AuthorityEventRow.action=='ingestion_accepted',AuthorityEventRow.subject_ref==ident))
                assert event.details['payload_digest']==row.payload_digest
                assert event.details['key_fingerprint']==row.key_fingerprint
            for table in ('diagnostic_ingestion_grants','diagnostic_authority_events'):
                with pytest.raises(PersistenceError):
                    async with rt.database.transaction() as s:await s.execute(text('DELETE FROM app.'+table))
            # New correlation is still rejected if its capture precedes the resource head.
            earlier=p.model_copy(update={'envelope_id':uuid4(),'correlation_id':'earlier','captured_at':p.captured_at-timedelta(seconds=1)})
            with pytest.raises(DiagnosticDenied):await svc.ingest(o,sign(earlier,key))
            later=p.model_copy(update={'envelope_id':uuid4(),'correlation_id':'later','captured_at':p.captured_at+timedelta(microseconds=1)})
            assert await svc.ingest(o,sign(later,key))==later.envelope_id
    asyncio.run(run())
