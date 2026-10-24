"""Compatibility facade for the centralized trusted output-safety policy."""
from tools.output_safety import POLICY, SAFE_REQUEST, NOTICE, safe_text, unsafe_request
from models.evidence import EvidenceGap


def safe_gaps(gaps):
    result=[]
    for gap in gaps:
        if (unsafe_request(gap.required_to_resolve,request_context=True)
                or unsafe_request(gap.description,request_context=True)):
            result.append(EvidenceGap(
                description=NOTICE,
                required_to_resolve=SAFE_REQUEST))
        else:
            result.append(gap)
    return result
