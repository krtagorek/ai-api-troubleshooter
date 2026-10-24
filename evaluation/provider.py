"""Real OpenAI-only adapter. No provider fallback and no diagnostic HTTP tools."""
import json
import os
import time
import copy
import httpx
from langchain_openai import ChatOpenAI
from tools.redaction import redact,install_redaction_filters,redact_message_content
from evaluation.budget import BudgetStopped
from evaluation.audit_metrics import provider_usage


def strict_root_schema(schema):
    result=copy.deepcopy(schema.model_json_schema())
    def visit(node):
        if isinstance(node,dict):
            node.pop('default',None)
            if 'properties' in node:
                node['additionalProperties']=False
                node['required']=list(node['properties'])
            for value in node.values():visit(value)
        elif isinstance(node,list):
            for value in node:visit(value)
    visit(result)
    return result


class ProviderStopped(RuntimeError): pass


def provider_schema(schema,messages):
    """Constrain live structured output to the actual trusted handle envelope.

    Historical parsers remain permissive; this schema is provider-bound only.
    """
    marker='UNTRUSTED_EVIDENCE_JSON\n'
    payload=next((json.loads(m.content.split(marker,1)[1]) for m in reversed(messages)
                  if isinstance(m.content,str) and m.content.startswith(marker)),{})
    strict_root=schema.__name__=='RootCauseAnalystOutput'
    if payload.get('evidence_contract')!='evidence-handles-v1':
        return strict_root_schema(schema) if strict_root else schema
    result=strict_root_schema(schema) if strict_root else copy.deepcopy(schema.model_json_schema())
    handles=[e['handle'] for e in payload.get('evidence_catalog',[])]
    props=result['properties']
    for key in ('evidence_contract','evidence_snapshot'):
        props[key]={'type':'string','enum':[payload[key]]}
        if key not in result.setdefault('required',[]):result['required'].append(key)
    def visit(node):
        if not isinstance(node,dict):return
        for key,value in node.get('properties',{}).items():
            if key in {'evidence_references','selected_evidence_handles','supporting_evidence_ids',
                       'contradicting_evidence_ids','discriminating_evidence_ids','evidence_ids'}:
                value['items']={'type':'string','enum':handles}
            if key=='selected_evidence_ids':value['maxItems']=0
        for value in node.values():
            if isinstance(value,dict):visit(value)
            elif isinstance(value,list):
                for entry in value:visit(entry)
    visit(result)
    # Live investigator may select handles/gaps, never redefine legacy items.
    if schema.__name__=='LogInvestigatorOutput':
        result['$defs']['EvidenceCollection']['properties']['items']['maxItems']=0
    result['title']=schema.__name__
    return result


def configuration_check():
    key=os.getenv('OPENAI_API_KEY','').strip()
    if not key or key.startswith(('your-','replace-')): return {'ready':False,'reason':'OPENAI_API_KEY is missing or a placeholder; configure it privately in .env'}
    if any(os.getenv(k) for k in ('OPENAI_BASE_URL','OPENAI_API_BASE')) or any(os.getenv(k,'').lower() in ('1','true','yes') for k in ('LANGCHAIN_TRACING_V2','LANGSMITH_TRACING')):
        return {'ready':False,'reason':'Custom provider endpoints or tracing environment variables are not permitted for this evaluation'}
    return {'ready':True,'reason':'Configuration present; connectivity has NOT been tested'}


class MeteredOpenAI:
    def __init__(self,model,budget,journal):
        install_redaction_filters()
        self.model,self.budget,self.journal=model,budget,journal
        self.stopped=False

    def with_structured_output(self,schema,**kwargs):
        owner=self
        class Structured:
            def invoke(self,messages):
                # Reserve for the full input cap, including structured-output schema overhead.
                if owner.stopped: raise ProviderStopped('Provider stopped; explicit new approval required')
                clean=[m.model_copy(update={'content':redact_message_content(m.content)}) for m in messages]
                wire_schema=provider_schema(schema,clean)
                serialized=json.dumps({'messages':[m.content for m in clean],'schema':schema.model_json_schema()},ensure_ascii=False)
                if len(serialized.encode())+4096>owner.budget.input_cap:
                    owner.budget.stopped='input_too_large'; raise BudgetStopped('Input byte envelope exceeds reserved token ceiling')
                row=owner.budget.reserve(); row['stage']=schema.__name__; started=time.monotonic(); owner.journal()
                try:
                    def request_guard(request):
                        # Includes roles, complete system/user messages, output
                        # schema and SDK wrappers/options as actually serialized.
                        row['provider_request_bytes']=len(request.content)
                        row['input_envelope_bytes_with_margin']=len(request.content)+4096
                        if len(request.content)+4096>owner.budget.input_cap:
                            owner.budget.stopped='provider_request_too_large'
                            raise BudgetStopped('Complete provider request exceeds reserved input envelope')
                    # Fixed official origin, no proxy credentials, redirects, retries, streaming or tools.
                    with httpx.Client(trust_env=False,follow_redirects=False,timeout=30,event_hooks={'request':[request_guard]}) as http_client:
                        client=ChatOpenAI(model=owner.model,api_key=os.environ['OPENAI_API_KEY'],base_url='https://api.openai.com/v1',
                            timeout=30,max_retries=0,max_completion_tokens=owner.budget.output_cap,http_client=http_client,
                            cache=False,temperature=0,store=False)
                        options={'method':'json_schema','include_raw':True}
                        if schema.__name__ in ('LiveLogInvestigatorOutput','RootCauseAnalystOutput'):options['strict']=True
                        result=client.with_structured_output(wire_schema,**options).invoke(clean)
                    raw=result['raw']; usage=raw.usage_metadata or {}
                    row['provider_response']=redact(getattr(raw,'content',None))
                    row['provider_usage_metadata']=provider_usage(usage)
                    row['provider_model']=raw.response_metadata.get('model_name')
                    owner.budget.settle(row,usage.get('input_tokens'),usage.get('output_tokens'),time.monotonic()-started)
                    if result.get('parsing_error') or result.get('parsed') is None: raise ProviderStopped('Structured output rejected')
                    original=result['parsed']
                    row['provider_structured_output']=redact(original.model_dump(mode='json') if hasattr(original,'model_dump') else original)
                    parsed=schema.model_validate(original)
                    row['structured_output']=redact(parsed.model_dump(mode='json'))
                    return parsed
                except BudgetStopped:
                    raise
                except Exception:
                    if row['status']=='reserved':
                        owner.stopped=True
                        row.update(status='provider_error_or_unknown_usage',latency_seconds=round(time.monotonic()-started,4))
                    raise ProviderStopped('OpenAI request or structured output failed; details withheld') from None
                finally: owner.journal()
        return Structured()
