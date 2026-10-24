"""Human reference-review gate; never infer or fill human decisions."""
import json
from datetime import datetime, timezone
from pathlib import Path

from data.benchmark_v2.build import DEFAULT, rows
from models.incident import Incident
from tools.evidence_catalog import build_catalog


def validate_reference_review(path, case_ids):
    errors = []
    reviewed = 0
    data = json.loads(Path(path).read_text())
    cases = data.get('cases', [])
    if sorted(c.get('incident_id','') for c in cases) != sorted(case_ids):
        errors.append('Review must contain each selected case exactly once')
    inputs = {r['incident_id']:r for r in rows(DEFAULT/'inference/development.jsonl')}
    for ident in case_ids:
        count_before = len(errors)
        matches = [c for c in cases if c.get('incident_id') == ident]
        if len(matches) != 1: continue
        review = matches[0].get('preoutput_reference_review') or {}
        for name in ('reviewer_id','rationale','injected_vs_inferable_cause'):
            if not isinstance(review.get(name),str) or not review[name].strip():
                errors.append(f'{ident}: missing {name}')
        try:
            timestamp = datetime.fromisoformat(review['timestamp'].replace('Z','+00:00'))
            if timestamp.tzinfo is None or timestamp > datetime.now(timezone.utc): raise ValueError()
        except (KeyError,ValueError,TypeError,AttributeError):
            errors.append(f'{ident}: valid nonfuture timezone-aware timestamp required')
        if review.get('source_sufficiency') not in ('sufficient','ambiguous','insufficient'):
            errors.append(f'{ident}: invalid source_sufficiency')
        if type(review.get('selection_set_exhaustive')) is not bool:
            errors.append(f'{ident}: explicit selection_set_exhaustive boolean required')
        alternatives = review.get('accepted_alternatives')
        if not isinstance(alternatives,list) or any(not isinstance(v,str) or not v.strip() for v in alternatives):
            errors.append(f'{ident}: explicit accepted_alternatives list required')
        catalog = {e.reference for e in build_catalog(Incident.model_validate(inputs[ident])).values()}
        groups = []
        for name in ('required_references','optional_references','irrelevant_references'):
            value = review.get(name)
            if (not isinstance(value,list) or any(not isinstance(v,str) for v in value)
                or (isinstance(value,list) and all(isinstance(v,str) for v in value)
                    and (len(set(value)) != len(value) or not set(value) <= catalog))):
                errors.append(f'{ident}: invalid {name}')
                groups.append(set())
            else: groups.append(set(value))
        if any(groups[a] & groups[b] for a,b in ((0,1),(0,2),(1,2))):
            errors.append(f'{ident}: reference relevance sets overlap')
        if review.get('source_sufficiency') == 'sufficient' and not groups[0]:
            errors.append(f'{ident}: sufficient assessment needs required evidence references')
        if len(errors) == count_before: reviewed += 1
    return {'complete':not errors,'reviewed_cases':reviewed,'errors':errors}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Offline human reference-review validator')
    parser.add_argument('--worksheet',type=Path,default=Path('reports/diagnostic-quality-paired-pilot/human-review-worksheet.json'))
    result = validate_reference_review(parser.parse_args().worksheet,['V2-01-01','V2-03-04'])
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['complete'] else 2)
