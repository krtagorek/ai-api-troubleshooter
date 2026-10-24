"""Conservative, label-free selection coverage; never infer a diagnosis."""
import re
from dataclasses import dataclass
from tools.evidence_catalog import build_catalog
from tools.redaction import redact
from tools.semantic_comparison import quality_limitation

BOILERPLATE=re.compile(r'^(?:request (?:unsuccessful|failed|could not be completed)|api request failed|upstream response inspected|outbound synthetic api request started|contract probe completed)$',re.I)
NOISE={'timestamp','time','ts','when','@timestamp','request_id','correlation_id','span_id','id','level','severity','event','status','status_code','environment','evidence_quality'}


@dataclass(frozen=True)
class SelectionAssessment:
    selected_ids: tuple[str,...]
    added_ids: tuple[str,...]
    omitted_ids: tuple[str,...]
    has_discriminating_candidates: bool
    review_complete: bool
    analysis_ready: bool
    quality_ids: tuple[str,...] = ()


def assess_selection(incident,selected_ids,*,max_added=32):
    """Deliver complementary existing facts, with deterministic bounded additions.

    Co-observed facts stay together: thresholds/timing, identifier comparisons,
    false/null observations and opposing observations are not filtered by value.
    This is a coverage heuristic, never proof of causal sufficiency.
    """
    catalog=build_catalog(incident)
    selected=list(selected_ids)
    if len(set(selected))!=len(selected) or any(i not in catalog for i in selected):
        raise ValueError('Selection must contain unique current catalog IDs')
    candidates=[]
    for ident,e in catalog.items():
        parts=e.reference.split('/')
        key=parts[-1]
        if key in NOISE: continue
        structured=e.reference.startswith('/logs/') and any(p in {'fields','diagnostics','records','measurements','observations'} for p in parts[3:])
        log_text=e.reference.startswith('/logs/') and key in {'message','body'} and not BOILERPLATE.fullmatch(e.observation.strip())
        response_detail=e.reference.startswith('/response_body/') and key in {'message','detail','reason','error'} and not BOILERPLATE.fullmatch(e.observation.strip())
        if structured or log_text or response_detail or quality_limitation(e):candidates.append(ident)
    missing=[i for i in candidates if i not in selected]
    # All-or-nothing: never deliver a threshold without its peer or silently
    # trim away contradictory records to satisfy a size limit.
    additions=missing if len(missing)<=max_added and all(redact(catalog[i].observation)==catalog[i].observation for i in missing) else []
    final=selected+additions
    omitted=[i for i in candidates if i not in final]
    groups=set()
    for ident in candidates:
        if quality_limitation(catalog[ident]):continue
        path=catalog[ident].reference
        # A single key/value record is one fact, not two independent observations.
        path=re.sub(r'(/records/\d+)/(?:name|value)(?:/.*)?$',r'\1',path)
        path=re.sub(r'/\d+$','',path)
        groups.add(path)
    return SelectionAssessment(tuple(final),tuple(additions),tuple(omitted),bool(candidates),not omitted,
                               not omitted and len(groups)>=2,
                               tuple(i for i in final if quality_limitation(catalog[i])))
