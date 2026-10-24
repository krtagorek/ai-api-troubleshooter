"""Pure, bounded contract evaluator. No URLs, tools, model rules or side effects."""
from datetime import datetime,timezone
from diagnostic_contracts.models import document
from diagnostic_contracts.ingestion import digest
from models.incident import Incident
from tools.evidence_catalog import resolve_evidence


def fencing_evaluation(snapshot,raw_contract,*,producer_label,resource,correlation,captured_at,now):
    contract=document(raw_contract)
    incident=Incident.model_validate(snapshot)
    if incident.model_dump(mode='json')!=snapshot:
        raise ValueError('Exact sanitized incident snapshot required')
    values={};refs=[]
    for rule in contract.fields:
        entry=resolve_evidence(incident,rule.pointer)
        node=snapshot
        for part in rule.pointer[1:].split('/'):
            part=part.replace('~1','/').replace('~0','~')
            node=node[int(part)] if isinstance(node,list) else node[part]
        expected={'integer':int,'string':str,'boolean':bool}[rule.data_type]
        if type(node) is not expected:raise ValueError('Contract scalar type mismatch')
        values[rule.role]=node;refs.append(entry.reference)
    if (values['domain']!=contract.domain or values['resource']!=resource or
        values['correlation']!=correlation or values['producer']!=producer_label or
        producer_label!=contract.authoritative_producer):raise ValueError('Source authority or scope mismatch')
    source_log=incident.logs[int(contract.fields[0].pointer.split('/')[2])]
    if source_log.get('correlation_id',correlation)!=correlation:
        raise ValueError('Outer correlation mismatch')
    observed=datetime.fromisoformat(values['observed_at'].replace('Z','+00:00'))
    if observed.tzinfo is None or observed!=captured_at or not 0<=(now-observed).total_seconds()<=min(120,contract.max_age_seconds):
        raise ValueError('Stale or unbound chronology')
    if (min(values[k] for k in ('routed_epoch','current_epoch','routed_term','current_term'))<0 or
        values['routed_term']!=values['current_term']):raise ValueError('Reset, wraparound or term mismatch')
    # Reachability is not health. No upstream cause is asserted by this result.
    if values['rejection']!=contract.rejection_code:raise ValueError('Missing or contradictory rejection')
    # Other records in the same correlation cannot contradict approved role values.
    for log in incident.logs:
        fields=log.get('fields',{})
        if fields.get('correlation',log.get('correlation_id'))!=correlation:continue
        for rule in contract.fields:
            key=rule.pointer.rsplit('/',1)[-1]
            if key in fields and (type(fields[key]) is not type(values[rule.role]) or fields[key]!=values[rule.role]):
                raise ValueError('Conflicting correlated evidence')
    qualified=values['routed_epoch']<values['current_epoch']
    return {'version':'fencing-semantic-result-1','qualified_analysis':qualified,'definitive_confirmation':False,
            'reason':'corroborated_stale_fencing_epoch' if qualified else 'no_supported_stale_epoch',
            'references':refs,'role_references':{rule.role:rule.pointer for rule in contract.fields},
            'snapshot_digest':digest(snapshot)}
