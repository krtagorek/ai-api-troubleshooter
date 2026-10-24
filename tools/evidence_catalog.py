"""Trusted incident-scoped scalar evidence catalog; no model or network calls."""
import hashlib
import json
import re
from dataclasses import dataclass

from models.incident import Incident
from models.evidence import REFERENCE_SOURCES, canonical_scalar, reference_source
from tools.redaction import LABELS, SENSITIVE


@dataclass(frozen=True)
class CatalogEntry:
    evidence_id: str
    reference: str
    source: str
    observation: str
    value_type: str


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def build_catalog(incident,*,max_entries=2048):
    incident=Incident.model_validate(incident)
    raw=incident.model_dump(mode='json')
    scope=_hash(raw)[:24]
    result={}
    def visit(value,path):
        if isinstance(value,dict):
            for key in sorted(value):
                if key in LABELS or SENSITIVE.search(key): continue
                visit(value[key],path+'/'+key.replace('~','~0').replace('/','~1'))
        elif isinstance(value,list):
            for index,item in enumerate(value): visit(item,path+'/'+str(index))
        else:
            observation=canonical_scalar(value)
            if not observation: return
            ident='ev:'+scope+':'+_hash([path,value])[:24]
            result[ident]=CatalogEntry(ident,path,reference_source(path),observation,
                                      'null' if value is None else type(value).__name__)
            if len(result)>max_entries: raise ValueError('Evidence catalog exceeds safe entry limit')
    for root in sorted(REFERENCE_SOURCES): visit(raw[root],'/'+root)
    return result


def resolve_evidence(incident,identifier):
    """IDs must match this exact sanitized incident snapshot.

    Legacy pointers may shed exactly one /incident prefix, only when the result
    is an existing allowlisted scalar catalog path. No fuzzy/path/URL decoding.
    """
    catalog=build_catalog(incident)
    if not isinstance(identifier,str): raise ValueError('Evidence identifier required')
    if identifier.startswith('ev:'):
        if identifier not in catalog: raise ValueError('Unknown or cross-incident evidence ID')
        return catalog[identifier]
    pointer=identifier[len('/incident'):] if identifier.startswith('/incident/') else identifier
    reference_source(pointer)
    if re.search(r'~(?![01])',pointer): raise ValueError('Invalid evidence pointer escape')
    for item in catalog.values():
        if item.reference==pointer: return item
    raise ValueError('Evidence reference does not resolve to a catalog scalar')


def catalog_payload(incident):
    return [vars(entry) for entry in build_catalog(incident).values()]


HANDLE_CONTRACT='evidence-handles-v1'


def handle_catalog(incident):
    """Short labels are meaningful ONLY inside the exact snapshot envelope."""
    entries=sorted(build_catalog(incident).values(),key=lambda e:e.reference)
    snapshot=_hash([vars(e) for e in entries])
    return {'evidence_contract':HANDLE_CONTRACT,'evidence_snapshot':snapshot,
            'evidence_catalog':[{'handle':f'E{i:02d}','reference':e.reference,'source':e.source,
                                 'observation':e.observation,'value_type':e.value_type} for i,e in enumerate(entries,1)]}


def resolve_handles(incident,handles,contract,snapshot):
    envelope=handle_catalog(incident)
    if contract!=HANDLE_CONTRACT or snapshot!=envelope['evidence_snapshot']:
        raise ValueError('Evidence contract or snapshot mismatch')
    if len(set(handles))!=len(handles): raise ValueError('Duplicate evidence handles')
    lookup={e['handle']:e['reference'] for e in envelope['evidence_catalog']}
    if any(h not in lookup for h in handles): raise ValueError('Unknown or malformed evidence handle')
    return [resolve_evidence(incident,lookup[h]) for h in handles]
