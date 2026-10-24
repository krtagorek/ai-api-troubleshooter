"""Sequential pre-call reservation; uncertain calls consume their full reservation."""
from dataclasses import dataclass,field
from decimal import Decimal

class BudgetStopped(RuntimeError): pass

@dataclass
class Budget:
    limit: Decimal
    input_rate: Decimal
    output_rate: Decimal
    max_calls: int
    input_cap: int=32768
    output_cap: int=1536
    reserved_or_spent: Decimal=Decimal(0)
    observed_cost: Decimal=Decimal(0)
    calls: list=field(default_factory=list)
    stopped: str | None=None

    def __post_init__(self):
        if any(not x.is_finite() or x<=0 for x in (self.limit,self.input_rate,self.output_rate)) or not 1<=self.max_calls<=201:
            raise ValueError('Positive finite rates/budget and a bounded call count are required')

    def reserve(self):
        cost=(self.input_cap*self.input_rate+self.output_cap*self.output_rate)/Decimal(1_000_000)
        if self.stopped or len(self.calls)>=self.max_calls or self.reserved_or_spent+cost>self.limit:
            self.stopped='budget_limit'; raise BudgetStopped('Call or cost limit reached before dispatch')
        self.reserved_or_spent+=cost
        row={'status':'reserved','reserved_usd':str(cost),'input_tokens':None,'output_tokens':None,'latency_seconds':None,'observed_usd':None}
        self.calls.append(row); return row

    def settle(self,row,input_tokens,output_tokens,latency):
        row['latency_seconds']=round(latency,4)
        if not all(isinstance(v,int) and not isinstance(v,bool) and v>=0 for v in (input_tokens,output_tokens)):
            self.stopped='usage_unavailable'; row['status']='usage_unavailable'; raise BudgetStopped('Provider usage missing; stopped conservatively')
        actual=(input_tokens*self.input_rate+output_tokens*self.output_rate)/Decimal(1_000_000)
        self.observed_cost+=actual; self.reserved_or_spent+=actual-Decimal(row['reserved_usd'])
        row.update(status='observed',input_tokens=input_tokens,output_tokens=output_tokens,observed_usd=str(actual))
        if input_tokens>self.input_cap or output_tokens>self.output_cap or self.reserved_or_spent>self.limit:
            self.stopped='reservation_exceeded'; raise BudgetStopped('Provider usage exceeded approved reservation; stopped')

    def summary(self):
        return {'calls':len(self.calls),'observed_input_tokens':sum(r['input_tokens'] or 0 for r in self.calls),
            'observed_output_tokens':sum(r['output_tokens'] or 0 for r in self.calls),'observed_cost_usd':str(self.observed_cost),
            'conservative_spend_usd':str(self.reserved_or_spent),'budget_usd':str(self.limit),
            'unresolved_calls':sum(r['status']!='observed' for r in self.calls),
            'total_call_latency_seconds':round(sum(r['latency_seconds'] or 0 for r in self.calls),4),
            'cost_basis':'Estimate from reported usage and approved rates; not an invoice. Unknown-usage calls retain full reservations.'}
