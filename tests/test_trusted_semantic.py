"""Generic synthetic semantic fixtures, not benchmark data."""
import copy
from datetime import datetime,timezone,timedelta
import pytest
from data.generate_incidents import generate
from models.incident import incident_from_record
from diagnostic_contracts.semantic import fencing_evaluation
from tests.test_diagnostic_registry import payload
from graph.workflow import build_workflow
from graph.state import initialize_state,serialize_state
from tests.test_workflow import fixtures


def sample():
    now=datetime.now(timezone.utc)
    incident=incident_from_record(generate()['development'][0])
    fields=dict(routed_epoch=4,current_epoch=6,routed_term=2,current_term=2,domain='demo-domain',resource='test',
                correlation='request-1',producer='owner-coordinator',observed_at=now.isoformat(),
                rejection='stale-fencing-epoch',reachability=True)
    snapshot=incident.model_copy(update={'logs':[{'message':'Fencing rejection','correlation_id':'request-1','fields':fields}]}).model_dump(mode='json')
    options=dict(producer_label='owner-coordinator',resource='test',correlation='request-1',captured_at=now,now=now)
    return snapshot,payload(),options


def test_positive_qualified_only_and_equal_hold():
    s,c,o=sample();result=fencing_evaluation(s,c,**o)
    assert result['qualified_analysis'] and not result['definitive_confirmation']
    s['logs'][0]['fields']['routed_epoch']=6
    assert not fencing_evaluation(s,c,**o)['qualified_analysis']
    s['logs'][0]['fields']['routed_epoch']=7
    assert not fencing_evaluation(s,c,**o)['qualified_analysis']

@pytest.mark.parametrize('field,value',[
 ('domain','other-domain'),('resource','other'),('correlation','other'),('producer','untrusted'),
 ('routed_term',3),('current_epoch',None),('routed_epoch',True),('current_epoch',-1),
 ('routed_epoch','4'),('rejection','leader-overloaded'),('observed_at','not-a-time')])
def test_invalid_semantics_fail_closed(field,value):
    s,c,o=sample();s['logs'][0]['fields'][field]=value
    with pytest.raises(ValueError):fencing_evaluation(s,c,**o)


def test_scope_freshness_conflicting_records_and_no_health_inference():
    s,c,o=sample();s['logs'][0]['fields']['reachability']=False
    result=fencing_evaluation(s,c,**o)
    assert result['qualified_analysis'] and 'health' not in str(result)
    with pytest.raises(ValueError):fencing_evaluation(s,c,**{**o,'now':o['now']+timedelta(seconds=31)})
    s['logs'].append({'message':'Contradiction','correlation_id':'request-1','fields':{'current_epoch':3}})
    with pytest.raises(ValueError):fencing_evaluation(s,c,**o)


def test_flag_off_legacy_and_flag_on_without_authorization(monkeypatch):
    snapshot,_,_=sample();state=initialize_state(snapshot,'flag-test')
    agents,calls=fixtures();legacy=build_workflow(agents=agents,trusted_semantics_enabled=False).invoke(state)
    assert calls['analyst']==1 and 'trusted_semantic' not in legacy
    agents,calls=fixtures();held=build_workflow(agents=agents,trusted_semantics_enabled=True).invoke(state)
    assert calls['analyst']==0 and held['status']=='awaiting_evidence'
    assert held['trusted_semantic'].status=='denied'
    assert 'capability' not in serialize_state(held)
    monkeypatch.setenv('DIAGNOSTIC_CONTRACTS_ENABLED','unrecognized')
    with pytest.raises(ValueError):build_workflow(agents=agents)


def test_descriptive_checkpoint_result_cannot_contain_authority_or_wrong_snapshot():
    from graph.state import TrustedSemanticResult,validate_state
    from diagnostic_contracts.ingestion import digest
    from uuid import uuid4
    snapshot,_,_=sample();state=initialize_state(snapshot,'descriptive-only')
    record=dict(version='trusted-semantic-1',status='qualified',result_ref=str(uuid4()),
        snapshot_digest=digest(state['sanitized_inputs'].model_dump(mode='json')),result_digest='a'*64)
    with pytest.raises(ValueError):TrustedSemanticResult.model_validate({**record,'capability':'forbidden'})
    with pytest.raises(ValueError):TrustedSemanticResult.model_validate({**record,'result_ref':None})
    with pytest.raises(ValueError):validate_state({**state,'trusted_semantic':{**record,'snapshot_digest':'b'*64}})
    from persistence.checkpoints import SanitizedSerializer
    obj=TrustedSemanticResult.model_validate(record);codec=SanitizedSerializer()
    assert codec.loads_typed(codec.dumps_typed(obj))==obj
