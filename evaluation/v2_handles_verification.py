"""Phase 13.11 offline preparation; never grants paid approval."""
from decimal import Decimal
from pathlib import Path
from evaluation.dataset import digest
from evaluation.v2_single import prepare as base_prepare, SingleCaseAdapter
from evaluation.v2 import V2Adapter
from tools.evidence_catalog import handle_catalog,HANDLE_CONTRACT

LEDGER=Path('reports/phase139-single-real-run/evaluation.json')
MARKER=Path('reports/.v2-single-dispatch-3177c14481e67d92b48a0563dca0037d2d836754c55f4a1129945d0e9330e59b.json')


def prepare():
    result=base_prepare(LEDGER,MARKER,Decimal('.0188840'))
    result.pop('approval_sha256')
    single=SingleCaseAdapter(V2Adapter(result['plan_path']))
    envelope=handle_catalog(single.inputs()[0])
    result.update(version='v2-live-handles-verification-1',evidence_contract=HANDLE_CONTRACT,
                  handle_catalog_sha256=digest(envelope),evidence_snapshot=envelope['evidence_snapshot'],
                  provider_contract='Live JSON schema enumerates supplied handles and exact version/snapshot; historical parser remains compatible.',
                  input_reservation_policy='Complete serialized provider JSON body bytes plus 4096 margin <=32768; output <=1536 tokens; provider usage checked after response.',
                  limitations=['Byte envelope includes all visible request overhead, not a guarantee of unobservable provider-internal token accounting.',
                               'Offline CI transport only; live reliability and semantic quality not yet established.'])
    result['approval_sha256']=digest(result)
    return result
