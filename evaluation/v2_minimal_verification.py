"""Prepare the reviewed single-case minimal-investigator pilot, never approve it."""
from decimal import Decimal
from pathlib import Path
import json
from evaluation.v2_single import prepare as base_prepare,SingleCaseAdapter
from evaluation.v2_execution import sha
from evaluation.v2 import V2Adapter
from evaluation.dataset import digest
from tools.evidence_catalog import handle_catalog
from graph.state import LiveLogInvestigatorOutput

LEDGER=Path('reports/phase1311-live-handles-real-run/evaluation.json')
MARKER=Path('reports/.v2-single-dispatch-e7601348d5178539a25ce16a1a32a30a5c9ebd1bb60a40cf29428a94a20f5c0d.json')
PREVIOUS=Path('reports/.v2-single-dispatch-3177c14481e67d92b48a0563dca0037d2d836754c55f4a1129945d0e9330e59b.json')


def prepare():
    result=base_prepare(LEDGER,MARKER,Decimal('.0225020'));result.pop('approval_sha256')
    bindings={}
    for marker in (PREVIOUS,MARKER):
        record=json.loads(marker.read_text());ledger=Path(record['output'])/'evaluation.json'
        previous=json.loads(ledger.read_text());usage=previous['usage']
        if usage['unresolved_calls'] or record['manifest_sha256']!=previous['approval_sha256']:
            raise ValueError('Historical dispatch requires reconciliation')
        if Decimal(previous['carried_prior_spend_usd'])+Decimal(previous['new_estimated_cost_usd'])!=Decimal(usage['conservative_spend_usd']):
            raise ValueError('Historical budget chain mismatch')
        bindings[str(marker.resolve())]={'marker_sha256':sha(marker),'ledger_path':str(ledger.resolve()),'ledger_sha256':sha(ledger)}
    incident=SingleCaseAdapter(V2Adapter(result['plan_path'])).inputs()[0]
    result.update(version='v2-minimal-investigator-pilot-1',reconciled_dispatches=bindings,
                  evidence_contract='evidence-handles-v1',handle_catalog_sha256=digest(handle_catalog(incident)),
                  live_investigator_schema_sha256=digest(LiveLogInvestigatorOutput.model_json_schema()),
                  live_investigator_fields=['evidence_contract','evidence_snapshot','selected_evidence_handles','completeness','diagnostic_gaps'],
                  strict_structured_output=True,legacy_live_fallback=False)
    result['approval_sha256']=digest(result)
    return result
