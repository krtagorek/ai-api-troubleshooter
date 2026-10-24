"""Trusted server composition only. Authorization never comes from graph state."""
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID
import asyncio
from diagnostic_contracts.ingestion import digest,Envelope
from diagnostic_contracts.permits import PermitService
from diagnostic_contracts.semantic import fencing_evaluation
from persistence.ingestion import IngestionEnvelope
from tools.evidence_catalog import resolve_evidence
from models.evidence import Evidence

analysis_context=ContextVar('trusted_semantic_analysis',default=None)

@dataclass(frozen=True)
class Binding:
    actor: object
    tenant_id: UUID
    api_id: UUID
    envelope_id: UUID

class SemanticIntegration:
    def __init__(self,ingestion,*,binding_resolver,producer_labels):
        self.ingestion=ingestion
        self.binding_resolver=binding_resolver
        self.producer_labels=dict(producer_labels)

    async def _prepare(self,state,*,operation="qualified_semantic_evaluation_v1",subject_digest=None):
        incident=state['sanitized_inputs'];snapshot=incident.model_dump(mode='json');snapshot_hash=digest(snapshot)
        # Resolver is trusted server code; it must reauthenticate the worker actor.
        # Only the thread and snapshot digest are selectors, never authority.
        binding=await asyncio.wait_for(self.binding_resolver(state['thread_id'],snapshot_hash),2)
        if not isinstance(binding,Binding):raise ValueError('Trusted binding required')
        async with self.ingestion.database.transaction() as s:
            row=await s.get(IngestionEnvelope,binding.envelope_id)
            if row is None or (row.tenant_id,row.api_id,row.snapshot_digest)!=(binding.tenant_id,binding.api_id,snapshot_hash):
                raise ValueError('Exact provenance binding required')
            envelope=Envelope.model_validate_json(__import__('json').dumps(row.document))
        p=envelope.payload
        # Never infer an authoritative producer name from a log or model output.
        label=self.producer_labels.get((p.producer_id,p.source))
        if not label:raise ValueError('Producer semantic authority is not configured')
        def pure(current,contract):
            return fencing_evaluation(current,contract,producer_label=label,resource=p.resource,
                correlation=p.correlation_id,captured_at=p.captured_at,now=self.ingestion.clock())
        permits=PermitService(self.ingestion,evaluator=pure,require_active=True,operation=operation,subject_digest=subject_digest)
        args=dict(tenant_id=binding.tenant_id,api_id=binding.api_id,snapshot_digest=snapshot_hash)
        permit=await permits.issue(binding.actor,binding.envelope_id,**args)
        return binding,permits,permit,args

    async def prepare_publication(self,state):
        from graph.state import serialize_state
        subject=digest(__import__('json').loads(serialize_state(state)))
        binding,permits,permit,args=await self._prepare(state,operation='diagnostic_report_publication_v1',subject_digest=subject)
        return Publication(binding,permits,permit,args,subject)

    async def evaluate(self,state):
        incident=state['sanitized_inputs'];snapshot_hash=digest(incident.model_dump(mode='json'))
        binding,permits,permit,args=await self._prepare(state)
        result=await permits.evaluate(binding.actor,permit,**args)
        # Permit/capability dies here. Checkpoints contain only descriptive refs.
        record={'version':'trusted-semantic-1','status':'qualified' if result['qualified_analysis'] else 'held',
            'result_ref':str(permit.permit_id),'snapshot_digest':snapshot_hash,'result_digest':digest(result)}
        additions=[]
        for reference in result['references']:
            entry=resolve_evidence(incident,reference)
            additions.append(Evidence(evidence_id=entry.evidence_id,source=entry.source,observation=entry.observation,
                                      reference=entry.reference,reliability=1.0))
        return record,additions,result


@dataclass(frozen=True)
class Publication:
    binding: Binding
    permits: object
    permit: object
    args: dict
    subject_digest: str

    async def check(self,session,state):
        from graph.state import serialize_state
        if digest(__import__('json').loads(serialize_state(state)))!=self.subject_digest:
            raise ValueError('Publication state changed')
        result=await self.permits.evaluate(self.binding.actor,self.permit,**self.args,_session=session)
        if not result['qualified_analysis'] or result['definitive_confirmation']:
            raise ValueError('No authorized qualified publication')


async def publication_for(state,options):
    from graph.semantic_gate import enabled
    # Also protect previously contract-derived snapshots when a recovery worker's
    # local flag was switched off. Descriptive metadata cannot bypass publication.
    if not state.get('final_report') or not (enabled(options.get('trusted_semantics_enabled')) or state.get('trusted_semantic')):
        return state,None
    from graph.report_policy import publication_hold
    try:
        integration=options.get('trusted_semantics')
        if not isinstance(integration,SemanticIntegration):raise ValueError('Missing publication authority')
        return state,await integration.prepare_publication(state)
    except Exception:return publication_hold(state),None


def current_analysis(incident):
    """Ephemeral node-local context; never read a checkpoint as authorization."""
    context=analysis_context.get()
    if context and context['snapshot_digest']==digest(incident.model_dump(mode='json')):
        return dict(context)
    return None


async def check_publication(session,state,ticket):
    """Runs under the same transaction/locks as the report insert and projection."""
    if not state.get('final_report'):return state
    from graph.report_policy import publication_hold
    if ticket is None:
        if state.get('trusted_semantic'):return publication_hold(state)
        report=state['final_report']
        unvalidated_completion=(state['status']=='completed' and (
            not report.validation_results or report.validation_results[-1].status!='passed' or
            not state['validation_results'] or state['validation_results'][-1].status!='passed'))
        if unvalidated_completion or (report.validation_results and report.validation_results[-1].status=='NOT_TESTED') or (
                state['validation_results'] and state['validation_results'][-1].status=='NOT_TESTED'):
            from graph.report_policy import legacy_tentative_report_decision
            from graph.state import validate_state
            decision=legacy_tentative_report_decision(state)
            # Recreate the canonical legacy view, including truthful summary and
            # provenance limitations. A recovered checkpoint cannot add claims.
            from graph.workflow import _final_report
            matches=decision.eligible and report==_final_report({**state,'final_report':None})['final_report']
            if not decision.eligible or not matches:
                return validate_state({**state,'final_report':None,'status':'awaiting_evidence',
                    'stop_reason':decision.reason if not decision.eligible else 'report_state_mismatch',
                    'pending_user_questions':['Review the grounded diagnostic output before publication; no checks or fixes were executed.']})
        return state
    if not isinstance(ticket,Publication):return publication_hold(state)
    from diagnostic_contracts.service import DiagnosticDenied,DiagnosticConflict
    try:
        await ticket.check(session,state)
        return state
    except (DiagnosticDenied,DiagnosticConflict,ValueError,TimeoutError,AttributeError):
        return publication_hold(state)
