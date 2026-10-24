"""Declarative, bounded v1 semantics; no lifecycle authority in owner input."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
import hashlib,json,re
from tools.redaction import redact

class FieldRule(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid',strict=True)
    pointer: str
    role: Literal['routed_epoch','current_epoch','routed_term','current_term','domain','resource','correlation','producer','observed_at','rejection','reachability']
    data_type: Literal['integer','string','boolean']
    unit: Literal['ordinal','identifier','iso8601','boolean']

    @model_validator(mode='after')
    def bounded(self):
        if not re.fullmatch(r'/logs/(?:0|[1-9][0-9]{0,2})/fields/[a-z][a-z0-9_]{0,63}',self.pointer):
            raise ValueError('Only bounded scalar log pointers supported')
        expected=('integer','ordinal') if self.role in ('routed_epoch','current_epoch','routed_term','current_term') else ('boolean','boolean') if self.role=='reachability' else ('string','iso8601') if self.role=='observed_at' else ('string','identifier')
        if (self.data_type,self.unit)!=expected:raise ValueError('Role type/unit mismatch')
        return self

class ContractDocument(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid',strict=True,revalidate_instances='always')
    schema_version: Literal['diagnostic-contract-1']
    metric: Literal['fencing_epoch']
    domain: str=Field(pattern=r'^[a-z][a-z0-9_-]{0,63}$')
    authoritative_producer: str=Field(pattern=r'^[a-z][a-z0-9_-]{0,63}$')
    fields: tuple[FieldRule,...]=Field(min_length=11,max_length=11)
    ordering: Literal['routed_less_than_current_same_term']
    reset_policy: Literal['reject_term_change']
    wraparound_policy: Literal['reject']
    max_age_seconds: int=Field(ge=1,le=3600)
    correlation_policy: Literal['same_record_same_resource']
    rejection_code: Literal['stale-fencing-epoch']
    contradiction_policy: Literal['hold']
    inference_boundary: Literal['qualified_only_no_upstream_cause']

    @model_validator(mode='after')
    def roles(self):
        if len({r.role for r in self.fields})!=11 or len({r.pointer for r in self.fields})!=11:
            raise ValueError('Exactly one distinct pointer per required role')
        if len({r.pointer.rsplit('/',1)[0] for r in self.fields})!=1:raise ValueError('One correlation record required')
        return self

def document(value):
    # Revalidate even objects constructed using model_copy/model_construct.
    if isinstance(value,ContractDocument):value=value.model_dump(mode='json')
    if redact(json.loads(json.dumps(value)))!=json.loads(json.dumps(value)):
        raise ValueError('Secret-like contract metadata is forbidden')
    if isinstance(value,dict):
        value=dict(value)
        if isinstance(value.get('fields'),list):value['fields']=tuple(value['fields'])
    return ContractDocument.model_validate(value)

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
