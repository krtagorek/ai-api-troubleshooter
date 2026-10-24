"""Bounded documents and fail-closed services; no network or model."""
import pytest
from diagnostic_contracts.models import document


def payload():
    roles=['routed_epoch','current_epoch','routed_term','current_term','domain','resource','correlation','producer','observed_at','rejection','reachability']
    fields=[]
    for role in roles:
        kind,unit=('integer','ordinal') if role in roles[:4] else ('boolean','boolean') if role=='reachability' else ('string','iso8601') if role=='observed_at' else ('string','identifier')
        fields.append({'pointer':'/logs/0/fields/'+role,'role':role,'data_type':kind,'unit':unit})
    return {'schema_version':'diagnostic-contract-1','metric':'fencing_epoch','domain':'demo-domain',
        'authoritative_producer':'owner-coordinator','fields':fields,'ordering':'routed_less_than_current_same_term',
        'reset_policy':'reject_term_change','wraparound_policy':'reject','max_age_seconds':30,
        'correlation_policy':'same_record_same_resource','rejection_code':'stale-fencing-epoch',
        'contradiction_policy':'hold','inference_boundary':'qualified_only_no_upstream_cause'}


def test_valid_bounded_document():
    d=document(payload());assert document(d)==d


@pytest.mark.parametrize('field,value', [('schema_version','unknown'),('ordering','eval(x)'),('wraparound_policy','modulo'),('domain','http://127.0.0.1'),('approval_status','active'),('url','https://example.com')])
def test_untrusted_authority_and_execution_metadata_rejected(field,value):
    p=payload();p[field]=value
    with pytest.raises(ValueError):document(p)


def test_arbitrary_pointer_or_duplicate_role_rejected():
    p=payload();p['fields'][0]['pointer']='/request_metadata/Authorization'
    with pytest.raises(ValueError):document(p)
    p=payload();p['fields'][0]=p['fields'][1]
    with pytest.raises(ValueError):document(p)
