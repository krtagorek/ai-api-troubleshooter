"""Feature-gated wrapper of existing nodes; no new public SSE node codes."""
import asyncio
import os
from langchain_core.runnables import RunnableLambda
from diagnostic_contracts.ingestion import digest
from diagnostic_contracts.integration import SemanticIntegration,analysis_context
from graph.state import validate_state,TrustedSemanticResult
from agents.common import inconclusive


def enabled(value):
    if value is None:
        raw=os.getenv('DIAGNOSTIC_CONTRACTS_ENABLED','false').lower()
        if raw not in ('true','false'):raise ValueError('Diagnostic flag must be true or false')
        return raw=='true'
    if type(value) is not bool:raise ValueError('Diagnostic flag must be boolean')
    return value


async def acquire(state,integration):
    clean=validate_state(state)
    try:
        if not isinstance(integration,SemanticIntegration) or not clean['evidence'].items:
            raise ValueError('Missing trusted integration or grounded collection')
        record,additions,result=await integration.evaluate(clean)
        known={e.evidence_id for e in clean['evidence'].items}
        clean['evidence']=clean['evidence'].model_copy(update={'items':[
            *clean['evidence'].items,*[e for e in additions if e.evidence_id not in known]]})
        clean['trusted_semantic']=TrustedSemanticResult.model_validate(record)
        return validate_state(clean),result
    except Exception:
        clean['trusted_semantic']=TrustedSemanticResult(version='trusted-semantic-1',status='denied',
            snapshot_digest=digest(clean['sanitized_inputs'].model_dump(mode='json')))
        return validate_state(clean),None


def wrap(name,node,integration,*,validation_authorized=False):
    async def invoke(state):
        clean=validate_state(state)
        if name=='log_investigator':
            update=await node.ainvoke(clean)
            clean,result=await acquire({**clean,**update},integration)
            return {**update,'evidence':clean['evidence'],'trusted_semantic':clean['trusted_semantic']}
        # Always reacquire on entry, including resume directly at analyst or retry.
        clean,result=await acquire(clean,integration)
        if not result or not result['qualified_analysis']:
            return {'trusted_semantic':clean['trusted_semantic'],'diagnosis':inconclusive(
                'Trusted semantic authorization or evidence is insufficient; additional sanitized evidence is required'),
                'hypotheses':[],'proposed_fix':None,'final_report':None,'validation_results':[],
                'status':'awaiting_evidence','pending_user_questions':['Provide fresh authorized sanitized evidence.'],
                'stop_reason':'trusted_semantic_hold'}
        if clean['diagnosis'] and clean['diagnosis'].status=='supported':
            clean['diagnosis']=clean['diagnosis'].model_copy(update={'status':'tentative',
                'uncertainty':[*clean['diagnosis'].uncertainty,'No independent definitive confirmation was executed.']})
        from graph.report_policy import tentative_report_eligible
        if name=='final_report' and not validation_authorized and not tentative_report_eligible(clean):
            from models.validation import ValidationResult
            return {'trusted_semantic':clean['trusted_semantic'],'diagnosis':clean['diagnosis'],
                'validation_results':[ValidationResult(status='NOT_TESTED')],'final_report':None,
                'status':'awaiting_evidence','pending_user_questions':['Obtain approved controlled validation results.'],
                'stop_reason':'validation_not_executed'}
        context={**result,'qualified_analysis':True,'definitive_confirmation':False}
        token=analysis_context.set(context)
        try:update=await node.ainvoke(clean)
        finally:analysis_context.reset(token)
        if name=='validator' and not validation_authorized:
            from models.validation import ValidationResult
            update['validation_results']=[*clean['validation_results'],ValidationResult(status='NOT_TESTED',issues=['No approved validation tool executed'])]
        if 'diagnosis' not in update and clean['diagnosis'] is not None:update['diagnosis']=clean['diagnosis']
        diagnosis=update.get('diagnosis')
        if diagnosis and diagnosis.status=='supported':
            update['diagnosis']=diagnosis.model_copy(update={'status':'tentative','uncertainty':[
                *diagnosis.uncertainty,'Contract observations support qualified analysis only, not definitive upstream confirmation.']})
        return {**update,'evidence':clean['evidence'],'trusted_semantic':clean['trusted_semantic']}
    return RunnableLambda(lambda state:asyncio.run(invoke(state)),afunc=invoke)


def route(state):
    value=state.get('trusted_semantic')
    # Descriptive result only. The analyst wrapper still reacquires authorization.
    return 'root_cause_analyst' if value and value.status=='qualified' else 'request_user_input'
