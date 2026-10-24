"""Validate references and narrow coexistence guards, not general causal truth."""
import re


def validate_counterevidence(hypotheses,evidence):
    by_id={e.evidence_id:e for e in evidence.items}
    for hypothesis in hypotheses:
        explained=set()
        for assessment in hypothesis.evidence_assessments:
            if not set(assessment.evidence_ids)<=set(by_id):
                raise ValueError('Assessment cites unselected evidence')
            if assessment.relation=='supporting' and not set(assessment.evidence_ids)<=set(hypothesis.supporting_evidence_ids):
                raise ValueError('Supporting assessment differs from supporting references')
            if assessment.relation=='neutral' and set(assessment.evidence_ids)&set(hypothesis.contradicting_evidence_ids):
                raise ValueError('Neutral observation cannot also be a contradiction')
            if assessment.relation=='contradicting':
                if not set(assessment.evidence_ids)<=set(hypothesis.contradicting_evidence_ids):
                    raise ValueError('Contradiction assessment differs from contradicting references')
                explained.update(assessment.evidence_ids)
        if set(hypothesis.contradicting_evidence_ids)!=explained:
            raise ValueError('Every contradiction needs an explicit causal explanation and prediction')
        # Identifier discrepancies and generic status codes do not exclude
        # independent validity mechanisms. No incident/family labels are used.
        description=hypothesis.description.lower()
        dimensions=[]
        if re.search(r'expir|expiry',description):dimensions.append(r'expir|expiry|valid_until|current_time')
        if re.search(r'revok|revocation',description):dimensions.append(r'revok|revocation')
        for ident in hypothesis.contradicting_evidence_ids:
            reference=by_id[ident].reference or ''
            if dimensions and not any(re.search(d,reference,re.I) for d in dimensions):
                raise ValueError('Independent variable cannot exclude expiry/revocation; causes may coexist')
