"""Existing workflow and agents; labels join only AFTER label-free inference."""
from time import monotonic
from uuid import uuid4
from pydantic import BaseModel,ConfigDict,Field
from langchain_core.messages import HumanMessage,SystemMessage
from agents import error_classifier,log_investigator,root_cause,resolution,validator
from graph.workflow import build_workflow
from graph.state import initialize_state
from evaluation.metrics import score,aggregate
from evaluation.provider import MeteredOpenAI
from evaluation.reporting import write_report


class ConnectivityReply(BaseModel):
    model_config=ConfigDict(extra='forbid',hide_input_in_errors=True)
    connectivity: bool=Field(description='Return true for this connectivity preflight')


def run_cases(selected,labels,model,budget,directory,report,*,stop_on_failure=False,scoring_adapter=None):
    report['cases']=[]
    def journal():
        report['usage']=budget.summary();report['calls']=budget.calls
        if budget.calls: report['real_model_execution']=True
        write_report(directory,report)
    llm=MeteredOpenAI(model,budget,journal)
    report['status']='preflight_running';journal()
    result=llm.with_structured_output(ConnectivityReply).invoke([SystemMessage(content='Return the requested structured connectivity response.'),HumanMessage(content='Return connectivity=true.')])
    if result.connectivity is not True: raise RuntimeError('Connectivity preflight failed')
    report['preflight']='passed';report['real_model_execution']=True;report['status']='running';journal()
    nodes={'classifier':error_classifier.create_agent(llm=llm),'log_investigator':log_investigator.create_agent(llm=llm),
        'root_cause_analyst':root_cause.create_agent(llm=llm),'resolution_generator':resolution.create_agent(llm=llm),'validator':validator.create_agent(llm=llm)}
    current_incident=None
    report['agent_events']=[]
    def observed_node(name,node):
        def invoke(state):
            event={'event_id':len(report['agent_events'])+1,'incident_id':current_incident,'agent':name,'status':'started'}
            report['agent_events'].append(event);journal();started=monotonic()
            try:
                update=node(state)
            except Exception:
                report['agent_events'].append({**event,'event_id':len(report['agent_events'])+1,'status':'failed','latency_seconds':round(monotonic()-started,4)})
                journal();raise
            report['agent_events'].append({**event,'event_id':len(report['agent_events'])+1,'status':'completed','latency_seconds':round(monotonic()-started,4)})
            journal();return update
        return invoke
    nodes={name:observed_node(name,node) for name,node in nodes.items()}
    graph=build_workflow(agents=nodes) # No external diagnostic tools, validation adapters or fix execution.
    for record in selected:
        current_incident=record['incident_id']
        started=monotonic();first=len(budget.calls)
        try:
            state=graph.invoke(initialize_state(record,str(uuid4()),max_retries=0))
            if getattr(budget,'stopped',None) or llm.stopped: raise RuntimeError('Evaluation stopped')
            # Ground-truth lookup is inaccessible to the graph and model dependency.
            metrics=scoring_adapter.score(state) if scoring_adapter is not None else score(state,labels[record['incident_id']])
            case={'incident_id':record['incident_id'],'status':'scored','workflow_status':state['status'],'metrics':metrics}
            if scoring_adapter is not None:
                from evaluation.v2 import state_to_json
                case['sanitized_output']=state_to_json(state)
        except Exception:
            case={'incident_id':record['incident_id'],'status':'failed','reason':'Budget/provider/structured-output or agent evidence checks rejected this case','metrics':None}
        case.update(latency_seconds=round(monotonic()-started,4),call_indices=list(range(first,len(budget.calls))))
        report['cases'].append(case)
        report['metrics']=scoring_adapter.summarize(report['cases']) if scoring_adapter is not None else aggregate(report['cases'],selected)
        if scoring_adapter is not None:
            report['metrics']['agent_execution_coverage']={name:{status:sum(e['agent']==name and e['status']==status for e in report['agent_events']) for status in ('started','completed','failed')} for name in nodes}
        journal()
        if getattr(budget,'stopped',None) or llm.stopped or (stop_on_failure and case['status']=='failed'):
            report['status']='stopped';break
    else: report['status']='completed' if all(c['status']=='scored' for c in report['cases']) else 'completed_with_failures'
    journal();return report
