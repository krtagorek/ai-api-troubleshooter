"""Fixture-only semantic authority. Not imported by application or graph code.

All identities, ingestion attestations and approval events are synthetic trusted
inputs in this prototype; none constitutes production authentication.
"""
from datetime import datetime, timedelta
from enum import Enum
from hashlib import sha256
import json
import re
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from tools.evidence_catalog import build_catalog


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', hide_input_in_errors=True)


class Status(str, Enum):
    DRAFT='draft'
    VERIFIED='owner_verified'
    REVIEWED='security_reviewed'
    APPROVED='approved'
    ACTIVE='active'
    REVOKED='revoked'
    SUPERSEDED='superseded'


class EpochContract(Frozen):
    schema_version: str = 'diagnostic-contract-fixture-1'
    fixture_only: bool = True
    tenant_id: str
    owner_id: str
    api_registry_id: str
    approved_endpoint_revision: int = Field(ge=1)
    contract_id: str
    version: int = Field(ge=1)
    shared_metric: str = 'fencing_epoch'
    fencing_domain: str
    authoritative_producer: str
    max_age_seconds: int = Field(ge=1, le=3600)
    ordering: str = 'strict_integer_monotonic_within_term'
    unit: str = 'ordinal'
    reset_policy: str = 'reject_term_change'
    wraparound_policy: str = 'reject'
    supported_inference: str = 'qualified_stale_fencing_only'
    # The template is bounded to ONE log record, not arbitrary expressions.
    log_index: int = Field(ge=0, le=100)
    rejection_code: str = 'stale-fencing-epoch'

    @model_validator(mode='after')
    def bounded(self):
        exact={'schema_version':'diagnostic-contract-fixture-1','fixture_only':True,
               'shared_metric':'fencing_epoch','ordering':'strict_integer_monotonic_within_term',
               'unit':'ordinal','reset_policy':'reject_term_change','wraparound_policy':'reject',
               'supported_inference':'qualified_stale_fencing_only','rejection_code':'stale-fencing-epoch'}
        if any(getattr(self,k)!=v for k,v in exact.items()):
            raise ValueError('Unsupported diagnostic semantics')
        for name in ('tenant_id','owner_id','api_registry_id','contract_id','fencing_domain','authoritative_producer'):
            if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',getattr(self,name)):
                raise ValueError('Symbolic identifiers only; no URLs, scripts or credentials')
        return self

    def pointers(self):
        return {role:f'/logs/{self.log_index}/fields/{name}' for role,name in {
            'routed':'routed_epoch','current':'current_epoch','routed_term':'routed_term',
            'current_term':'current_term','domain':'fencing_domain','resource':'resource_id',
            'correlation':'correlation_id','authority':'producer_id','time':'observed_at',
            'rejection':'rejection','reachable':'leader_reachable',
        }.items()}


def fingerprint(value):
    if hasattr(value,'model_dump'):value=value.model_dump(mode='json')
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class Actor(Frozen):
    tenant_id: str
    actor_id: str
    roles: frozenset[str]


class Binding(Frozen):
    tenant_id: str
    api_registry_id: str
    endpoint_revision: int
    contract_id: str
    contract_version: int
    contract_digest: str
    snapshot_digest: str
    expected_domain: str
    expected_resource: str
    expected_correlation: str
    permit: str


class Decision(Frozen):
    status: str
    qualified_analysis: bool = False
    definitive_confirmation: bool = False
    reason: str
    observation_references: tuple[str,...] = ()
    permitted_hypothesis: str | None = None
    upstream_cause: None = None
    requires_human_approval: bool = True


class FixtureAuthority:
    """Ephemeral test authority, not a persistence/identity implementation."""
    def __init__(self, *, fixture_mode=False, verified_owners=frozenset()):
        if not fixture_mode:raise ValueError('Fixture mode required; production unsupported')
        self._owners=frozenset(verified_owners)
        self._contracts={};self._states={};self._permits={};self._ingestion=set();self._events=[];self._endpoints={}

    @property
    def audit(self):
        return tuple(json.loads(json.dumps(self._events)))

    def _event(self,kind,key,actor,now):
        if now.tzinfo is None:raise ValueError('Timezone-aware clock required')
        event={'sequence':len(self._events),'previous':self._events[-1]['digest'] if self._events else None,
               'kind':kind,'key':key,'actor':actor.actor_id,'timestamp':now.isoformat()}
        event['digest']=fingerprint(event);self._events.append(event)

    def register(self,actor,contract,now):
        if actor.tenant_id!=contract.tenant_id or actor.actor_id!=contract.owner_id or 'owner' not in actor.roles:
            raise ValueError('Owner authorization required')
        key=(contract.tenant_id,contract.contract_id,contract.version)
        if key in self._contracts:raise ValueError('Immutable version already registered')
        if any(k[:2]==key[:2] and k[2]>=key[2] for k in self._contracts):raise ValueError('Monotonic version required')
        self._event('draft',key,actor,now)
        self._contracts[key]=contract;self._states[key]=Status.DRAFT

    def transition(self,actor,ident,version,target,now):
        key=(actor.tenant_id,ident,version)
        if key not in self._contracts:raise ValueError('Contract unavailable')
        c=self._contracts[key];old=self._states[key]
        if target in (Status.REVOKED,Status.SUPERSEDED):
            allowed='admin' in actor.roles and old not in (Status.REVOKED,Status.SUPERSEDED)
        elif target==Status.VERIFIED:
            allowed=(old==Status.DRAFT and actor.actor_id==c.owner_id and
                     (actor.tenant_id,actor.actor_id) in self._owners)
        else:
            required={Status.REVIEWED:(Status.VERIFIED,'security_reviewer'),
                      Status.APPROVED:(Status.REVIEWED,'admin'),Status.ACTIVE:(Status.APPROVED,'admin')}
            prior,role=required.get(target,(None,None))
            allowed=(old==prior and role in actor.roles and actor.actor_id!=c.owner_id)
        if not allowed:raise ValueError('Unauthorized lifecycle transition')
        if target==Status.ACTIVE and any(k[:2]==key[:2] and self._states[k]==Status.ACTIVE for k in self._states):
            raise ValueError('Explicitly supersede previous version first')
        self._event(target.value,key,actor,now);self._states[key]=target

    def attest_fixture_ingestion(self,actor,incident,producer,now):
        if 'trusted_ingester' not in actor.roles:raise ValueError('Ingestion authority required')
        # A production implementation must validate signed transport provenance.
        self._event('fixture_ingestion',(actor.tenant_id,fingerprint(incident),producer),actor,now)
        self._ingestion.add((actor.tenant_id,fingerprint(incident),producer))

    def fixture_endpoint(self,actor,api_id,revision,approved,now):
        if 'admin' not in actor.roles:raise ValueError('Endpoint fixture administrator required')
        self._event('fixture_endpoint',(actor.tenant_id,api_id,revision,approved),actor,now)
        self._endpoints[(actor.tenant_id,api_id)]=(revision,approved)

    def authorize(self,actor,incident,ident,version,*,endpoint_revision,domain,resource,correlation,now):
        key=(actor.tenant_id,ident,version)
        if key not in self._contracts or 'investigator' not in actor.roles:raise ValueError('Contract unavailable')
        c=self._contracts[key]
        if (self._states[key]!=Status.ACTIVE or endpoint_revision!=c.approved_endpoint_revision
                or self._endpoints.get((actor.tenant_id,c.api_registry_id))!=(endpoint_revision,True)):
            raise ValueError('Active matching diagnostic approval required')
        if (actor.tenant_id,fingerprint(incident),c.authoritative_producer) not in self._ingestion:
            raise ValueError('Trusted observation provenance missing')
        b=Binding(tenant_id=actor.tenant_id,api_registry_id=c.api_registry_id,endpoint_revision=endpoint_revision,
            contract_id=ident,contract_version=version,contract_digest=fingerprint(c),snapshot_digest=fingerprint(incident),
            expected_domain=domain,expected_resource=resource,expected_correlation=correlation,permit=uuid4().hex)
        self._event('fixture_authorization',key,actor,now)
        self._permits[b.permit]=(b,now,now+timedelta(seconds=60),actor.actor_id)
        return b

    def evaluate(self,actor,binding,incident,evidence,now):
        def hold(reason):return Decision(status='hold',reason=reason)
        if now.tzinfo is None:return hold('Timezone-aware clock required')
        stored=self._permits.get(binding.permit)
        if not stored or stored[0]!=binding or not stored[1]<=now<=stored[2] or stored[3]!=actor.actor_id:return hold('Fresh authorization required')
        key=(actor.tenant_id,binding.contract_id,binding.contract_version)
        if actor.tenant_id!=binding.tenant_id or 'investigator' not in actor.roles:return hold('Tenant or actor denied')
        c=self._contracts.get(key)
        if c is None or self._states[key]!=Status.ACTIVE or fingerprint(c)!=binding.contract_digest:
            return hold('Revoked, superseded or unapproved contract')
        if (binding.endpoint_revision!=c.approved_endpoint_revision or fingerprint(incident)!=binding.snapshot_digest
                or self._endpoints.get((actor.tenant_id,c.api_registry_id))!=(binding.endpoint_revision,True)):
            return hold('Revision or snapshot mismatch')
        if (actor.tenant_id,binding.snapshot_digest,c.authoritative_producer) not in self._ingestion:
            return hold('Trusted provenance missing')
        catalog=build_catalog(incident);grounded={}
        for e in evidence.items:
            entry=catalog.get(e.evidence_id)
            if entry is None or (entry.reference,entry.source,entry.observation)!=(e.reference,e.source,e.observation) or e.ambiguous:
                return hold('Ungrounded or ambiguous observation')
            if entry.reference in grounded:return hold('Duplicate observation')
            grounded[entry.reference]=entry
        if evidence.completeness=='conflicting':return hold('Contradictory observations')
        refs=c.pointers()
        if any(pointer not in grounded for pointer in refs.values()):return hold('Required grounded observation missing')
        # All observations in this attested single-record format must have been
        # delivered. Omitted conflicting peers cannot be hidden by selection.
        for entry in catalog.values():
            if re.fullmatch(r'/logs/\d+/fields/[^/]+',entry.reference) and entry.reference not in grounded:
                return hold('Structured comparison context omitted')
        values={role:(grounded[ref].observation if grounded[ref].value_type=='str' else json.loads(grounded[ref].observation)) for role,ref in refs.items()}
        if (values['domain']!=c.fencing_domain or values['domain']!=binding.expected_domain or
                values['resource']!=binding.expected_resource or values['correlation']!=binding.expected_correlation or
                values['authority']!=c.authoritative_producer):return hold('Domain, resource, correlation or authority mismatch')
        epochs=[values[k] for k in ('routed','current','routed_term','current_term')]
        if any(type(v) is not int or v<0 for v in epochs):return hold('Nonnegative integer ordinals required')
        for log in incident.logs:
            other=log.get('fields',{})
            if (other.get('correlation_id')==values['correlation'] and
                    other.get('resource_id')==values['resource'] and other.get('fencing_domain')==values['domain']):
                for role in ('current','routed','rejection','authority'):
                    field=refs[role].rsplit('/',1)[-1]
                    if field in other and other[field]!=values[role]:return hold('Conflicting correlated evidence')
        if values['routed_term']!=values['current_term']:return hold('Reset or term transition not supported')
        if values['routed']>=values['current']:return hold('No supported stale ordering; wraparound not inferred')
        if values['rejection']!=c.rejection_code:return hold('Missing or contradictory rejection')
        if type(values['reachable']) is not bool:return hold('Reachability must be boolean')
        try:
            observed=datetime.fromisoformat(values['time'].replace('Z','+00:00'))
            if observed.tzinfo is None or not timedelta(0)<=now-observed<=timedelta(seconds=c.max_age_seconds):
                return hold('Stale or future observation')
        except (ValueError,TypeError,AttributeError):return hold('Invalid observation time')
        return Decision(status='qualified',qualified_analysis=True,
            reason='Approved same-term epoch ordering and rejection corroborate a stale fencing hypothesis; origin remains unconfirmed. Reachability is not health.',
            observation_references=tuple(refs.values()),permitted_hypothesis='stale_fencing_rejection')
