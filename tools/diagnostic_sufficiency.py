"""Label-free readiness to ATTEMPT qualified analysis, never confirmation."""
from dataclasses import dataclass
import itertools
import json
import re

from tools.evidence_catalog import build_catalog
from tools.evidence_selection import assess_selection
from tools.semantic_comparison import comparison, conflicting_bounds, numeric_role, numeric, quality_limitation


@dataclass(frozen=True)
class Readiness:
    qualified_analysis: bool
    definitive_confirmation: bool
    reason: str
    comparison_references: tuple[str,...] = ()


def readiness(incident,evidence):
    def hold(reason):return Readiness(False,False,reason)
    if evidence.completeness not in ('complete','partial') or not evidence.items:
        return hold('Unknown, missing or conflicting evidence')
    if any(e.ambiguous for e in evidence.items):return hold('Explicitly ambiguous observations')
    catalog=build_catalog(incident)
    ids=[e.evidence_id for e in evidence.items]
    if len(set(ids))!=len(ids):return hold('Duplicate evidence')
    for e in evidence.items:
        entry=catalog.get(e.evidence_id)
        if entry is None or (entry.reference,entry.source,entry.observation)!=(e.reference,e.source,e.observation):
            return hold('Evidence is not bound to this incident snapshot')
    coverage=assess_selection(incident,ids,max_added=0)
    if coverage.omitted_ids:return hold('Diagnostic comparison peers remain omitted')
    # Facts are grouped within one log record; no uncorrelated cross-log joins.
    groups={}
    by_ref={e.reference:e for e in catalog.values()}
    for ident in ids:
        e=catalog[ident]
        if not e.reference.startswith('/logs/') or quality_limitation(e):continue
        parts=e.reference.split('/')
        key=parts[-1]
        if key.isdigit():
            key=parts[-2];parent='/'.join(parts[:-2])
        else:parent='/'.join(parts[:-1])
        if key in ('name','value') and '/records/' in e.reference:
            if key=='name':continue
            label=by_ref.get(parent+'/name')
            if label is None or label.evidence_id not in ids:continue
            key=label.observation;parent=parent.rsplit('/',2)[0]
        value=e.observation if e.value_type=='str' else json.loads(e.observation)
        groups.setdefault(parent,{}).setdefault(key,[]).append((value,e.reference))
    # Contradictory repeated values at the same scope are held, not voted away.
    scoped={}
    for parent,facts in groups.items():
        index=int(parent.split('/')[2]);log=incident.logs[index]
        correlation=(log.get('correlation_id') or log.get('context',{}).get('request')
                     or log.get('trace',{}).get('id'))
        if correlation:
            for name,values in facts.items():
                if all(re.search(r'/\d+$',ref) for _,ref in values):continue
                scope=(str(correlation),str(log.get('event','')),str(log.get('stage','')),name)
                scoped.setdefault(scope,set()).update(json.dumps(v,sort_keys=True) for v,_ in values)
    if any(len(values)>1 for values in scoped.values()):
        return hold('Conflicting correlated observations; scope or chronology needs clarification')
    for facts in groups.values():
        if any(not all(re.search(r'/\d+$',ref) for _,ref in values)
               and len({json.dumps(v,sort_keys=True) for v,_ in values})>1 for values in facts.values()):
            return hold('Conflicting values at the same observation scope')
    role_pairs=(('configured','received'),('expected','actual'),('expected','observed'),
                ('resource','workload'),('server','client'))
    if any(conflicting_bounds(facts) for facts in groups.values()):
        return hold('Conflicting explicit bounds; request constraint clarification')
    for facts in groups.values():
        for left,right in role_pairs:
            for name,values in facts.items():
                words=name.split('_')
                if left not in words:continue
                other='_'.join(right if w==left else w for w in words)
                if other not in facts:continue
                if all(re.search(r'/\d+$',ref) for _,ref in values+facts[other]):
                    left_set={json.dumps(v,sort_keys=True) for v,_ in values}
                    right_set={json.dumps(v,sort_keys=True) for v,_ in facts[other]}
                    if left_set.isdisjoint(right_set):
                        return Readiness(True,False,'Disjoint observed capability sets permit qualified analysis',
                                         tuple(ref for _,ref in values+facts[other]))
                    continue
                for (a,ar),(b,br) in itertools.product(values,facts[other]):
                    numeric_key=numeric_role(name.replace('expected','actual'))
                    # Numeric configuration differences require an explicit bound;
                    # expected/actual numeric equality contracts need a known metric.
                    numeric_pair=numeric(a) and numeric(b)
                    if numeric_pair and (left!='expected' or numeric_key is None):continue
                    if not numeric_pair and numeric_key is not None:continue
                    if a is not None and b is not None and type(a) is type(b) and a!=b:
                        return Readiness(True,False,'Observed role-paired discrepancy permits a qualified hypothesis',(ar,br))
        matched=comparison(facts)
        if matched:
            return Readiness(True,False,'Observed same-metric bound violation permits qualified analysis',matched)
        for name,values in facts.items():
            if name in ('exception','exception_type','error_type'):
                for value,ref in values:
                    if isinstance(value,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]*(?:Error|Exception)',value):
                        return Readiness(True,False,'Specific observed exception permits qualified analysis',(ref,))
        if 'error_offset' in facts and 'decoder' in facts and 'content_type' in facts:
            value,ref=facts['error_offset'][0]
            if type(value) is int and value>=0:
                return Readiness(True,False,'Observed parsing failure with format/decoder context permits qualified analysis',
                                 (ref,facts['decoder'][0][1],facts['content_type'][0][1]))
    return hold('No supported comparative or specific diagnostic observation; request discriminating evidence')
