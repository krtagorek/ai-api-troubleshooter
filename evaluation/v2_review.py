"""Human-entered review/adjudication contracts; never manufacture human ratings."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from evaluation.dataset import digest

DIMENSIONS={'causal_reasoning_sufficient','uncertainty_ambiguous','evidence_grounding','resolution_safety','validation_truthfulness'}


class HumanReview(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    status: Literal['COMPLETED']='COMPLETED'
    incident_id: str
    reviewer_id: str=Field(min_length=1)
    output_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    rubric_version: Literal['v2-semantic-1']
    dimension_ratings: dict[str,int | None]
    applicability: dict[str,str]
    rationale: str=Field(min_length=10)
    evidence_references: list[str]=Field(min_length=1)
    hard_failures: list[str]

    @model_validator(mode='after')
    def ratings(self):
        if set(self.dimension_ratings)!=DIMENSIONS or set(self.applicability)!=DIMENSIONS:
            raise ValueError('Every rubric dimension needs an applicability decision')
        for name,rating in self.dimension_ratings.items():
            if rating is not None and (type(rating) is not int or not 0<=rating<=3): raise ValueError('Invalid rating')
            if not self.applicability[name].strip(): raise ValueError('Applicability reason required')
            if name=='validation_truthfulness' and rating not in (None,0,3): raise ValueError('Validation rubric permits only 0 or 3')
            if rating is None and not self.applicability[name].startswith(('N/A:', 'NOT_EXERCISED:')): raise ValueError('Missing ratings require explicit non-applicability')
        return self


def bind_review(review,scored_result):
    """Reject premature reviews and ratings for a different recorded output."""
    record=HumanReview.model_validate(review)
    if record.incident_id!=scored_result.get('incident_id') or record.output_sha256!=scored_result.get('output_sha256'):
        raise ValueError('Human review must match a recorded scored output')
    return record


def semantic_summary(reviews):
    """Human-only dimension means, with N/A counts; no automatic quality scores."""
    validated=[HumanReview.model_validate(r) for r in reviews]
    keys=[(r.incident_id,r.reviewer_id,r.output_sha256) for r in validated]
    if len(set(keys))!=len(keys): raise ValueError('Duplicate human review')
    return {'status':'HUMAN_RATINGS_RECORDED_NOT_AUTOMATICALLY_ADJUDICATED' if validated else 'PENDING_HUMAN_REVIEW',
            'review_records':len(validated),'reviewed_cases':len({r.incident_id for r in validated}),
            'dimensions':{k:{'mean':sum(values)/len(values) if values else None,'n':len(values),'not_applicable':len(validated)-len(values)}
                          for k in sorted(DIMENSIONS) for values in [[r.dimension_ratings[k] for r in validated if r.dimension_ratings[k] is not None]]}}


def compare_reviews(first,second):
    first=HumanReview.model_validate(first); second=HumanReview.model_validate(second)
    if first.reviewer_id==second.reviewer_id: raise ValueError('Independent reviewer identities required')
    if (first.incident_id,first.output_sha256)!=(second.incident_id,second.output_sha256): raise ValueError('Reviews must bind the same output')
    disagreements=[k for k in DIMENSIONS if first.dimension_ratings[k]!=second.dimension_ratings[k]]
    if set(first.hard_failures)!=set(second.hard_failures): disagreements.append('hard_failures')
    return {'incident_id':first.incident_id,'output_sha256':first.output_sha256,
            'review_hashes':[digest(first.model_dump()),digest(second.model_dump())],
            'reviewers':[first.reviewer_id,second.reviewer_id], 'disagreements':sorted(disagreements),
            'status':'NEEDS_ADJUDICATION' if disagreements else 'AGREED',
            'dimension_agreement':sum(first.dimension_ratings[k]==second.dimension_ratings[k] for k in DIMENSIONS)/len(DIMENSIONS)}


def adjudicate_reviews(first,second,adjudicator_id,decision,rationale):
    compared=compare_reviews(first,second)
    final=HumanReview.model_validate(decision)
    if not adjudicator_id or adjudicator_id in compared['reviewers'] or final.reviewer_id!=adjudicator_id: raise ValueError('Separate adjudicator required')
    if final.incident_id!=compared['incident_id'] or final.output_sha256!=compared['output_sha256'] or len(rationale.strip())<10: raise ValueError('Bound decision and rationale required')
    return {**compared,'status':'ADJUDICATED','adjudicator_id':adjudicator_id,'decision':final.model_dump(),'rationale':rationale}


def label_adjudication(original_label,proposal,*,reviewer_ids,adjudicator_id,decision,rationale):
    """Return a hash-bound sidecar; never edit frozen labels or infer approval."""
    if len(set(reviewer_ids))!=2 or any(not r.strip() for r in reviewer_ids): raise ValueError('Two label reviewers required')
    if not adjudicator_id or adjudicator_id in reviewer_ids: raise ValueError('Separate adjudicator required')
    if decision not in ('retain','propose_revision','exclude') or len(rationale.strip())<10: raise ValueError('Explicit label decision/rationale required')
    if proposal.get('incident_id')!=original_label['incident_id']: raise ValueError('Label identity mismatch')
    if set(proposal)!=set(original_label) or proposal.get('rubric_version')!='v2-semantic-1': raise ValueError('Proposal must preserve the label schema/version')
    return {'incident_id':original_label['incident_id'],'original_label_sha256':digest(original_label),
            'proposed_label_sha256':digest(proposal),'proposal':proposal,'reviewers':reviewer_ids,
            'adjudicator_id':adjudicator_id,'decision':decision,'rationale':rationale,
            'dataset_mutated':False,'application':'New benchmark revision and new frozen plan required; never apply this sidecar silently'}


def worksheet(incident_id):
    return {'incident_id':incident_id,'status':'WAITING_FOR_MODEL_OUTPUT_AND_HUMAN_REVIEW',
            'reviewer_id':None,'output_sha256':None,'rubric_version':'v2-semantic-1',
            'dimension_ratings':{k:None for k in sorted(DIMENSIONS)},
            'applicability':{k:'' for k in sorted(DIMENSIONS)},
            'evidence_references':[],'rationale':'','hard_failures':[]}
