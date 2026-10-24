"""Read-only validation of explicitly confirmed enhanced-pilot human reviews."""
import json
from datetime import datetime
from pathlib import Path

from evaluation.dataset import digest
from evaluation.quality_pilot import sha

ROOT=Path('reports/diagnostic-quality-enhanced-live-run')


def validate_review(path=ROOT/'semantic-review-worksheet.json'):
    worksheet=json.loads(Path(path).read_text())
    outputs=json.loads((ROOT/'reviewer-outputs.json').read_text())
    rubric_path=Path(worksheet['rubric_path'])
    if worksheet['source_outputs_sha256']!=sha(ROOT/'reviewer-outputs.json') or worksheet['rubric_sha256']!=sha(rubric_path):
        raise ValueError('Review output or rubric hash mismatch')
    rubric=json.loads(rubric_path.read_text())
    scales={**rubric['frozen_v2_dimensions'],**rubric['supplemental_dimensions']}
    reviews=worksheet['reviews']
    if len(reviews)!=2 or {r['incident_id'] for r in reviews}!={'V2-01-01','V2-03-04'}:
        raise ValueError('Review scope mismatch')
    for review in reviews:
        source=next(o for o in outputs if o['blind_id']==review['blind_id'])
        if source['state']['incident_id']!=review['incident_id'] or digest(source)!=review['output_sha256']:
            raise ValueError('Individual output hash mismatch')
        timestamp=datetime.fromisoformat(review['timestamp'])
        if (review['decision']!='CONFIRMED' or not review['reviewer_id'].strip()
                or timestamp.tzinfo is None):
            raise ValueError('Explicit identity, confirmation and timezone-aware timestamp required')
        if set(review['dimension_ratings'])!=set(scales):
            raise ValueError('Dimension mismatch')
        for dimension,scale in scales.items():
            rating=review['dimension_ratings'][dimension]
            applies=review['applicability'][dimension]
            if applies=='applicable':
                if type(rating) is not int or str(rating) not in scale:
                    raise ValueError('Rating not in exact saved rubric scale')
            elif applies not in ('not_applicable','not_exercised') or rating is not None:
                raise ValueError('Unexercised dimensions must remain unscored')
            if not review['rationales'][dimension].strip():
                raise ValueError('Human-confirmed rationale required')
        if review['hold_adjudication'] not in rubric['hold_adjudication']:
            raise ValueError('Invalid hold adjudication')
        if review['unsafe_credential_request_rating'] not in ('UNSAFE_WORDING','NO_UNSAFE_REQUEST_OBSERVED'):
            raise ValueError('Explicit credential safety finding required')
    return {'complete':True,'reviewed_cases':2,'protocol':'Single-reviewer exploratory development pilot',
            'reviewers':sorted({r['reviewer_id'] for r in reviews}),
            'independent_benchmark_accuracy_claimed':False,'output_and_rubric_hashes':'PASS'}


if __name__=='__main__':
    print(json.dumps(validate_review(),indent=2))
