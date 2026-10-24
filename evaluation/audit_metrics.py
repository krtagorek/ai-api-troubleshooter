"""Narrow numeric accounting projection; generic evidence redaction stays strict."""
from tools.redaction import redact

COUNTS = {'input_tokens','output_tokens','total_tokens'}
DETAILS = {'input_token_details':{'cache_read','cache_creation','audio'},
           'output_token_details':{'reasoning','audio'}}
TOTALS = {'observed_input_tokens','observed_output_tokens'}


def counter(value):
    return type(value) is int and value >= 0


def provider_usage(value):
    """Drop everything except explicitly recognized nonnegative integer counts."""
    if not isinstance(value,dict): return {}
    result = {k:v for k,v in value.items() if k in COUNTS and counter(v)}
    for key, allowed in DETAILS.items():
        detail=value.get(key)
        if isinstance(detail,dict):
            result[key]={k:v for k,v in detail.items() if k in allowed and counter(v)}
    return result


def accounting_event(kind, value):
    result=redact(value)
    if kind != 'actual_provider_accounting': return result
    for original, clean in zip(value.get('calls',[]),result.get('calls',[])):
        for key in COUNTS:
            if counter(original.get(key)): clean[key]=original[key]
        if isinstance(original.get('provider_usage_metadata'),dict):
            clean['provider_usage_metadata']=provider_usage(original['provider_usage_metadata'])
    usage=value.get('usage',{})
    for key in TOTALS:
        if counter(usage.get(key)): result['usage'][key]=usage[key]
    return result


def artifact(name, value):
    result=redact(value)
    if name=='summary.json' and isinstance(value.get('actual_usage'),dict):
        for key in TOTALS:
            if counter(value['actual_usage'].get(key)):
                result['actual_usage'][key]=value['actual_usage'][key]
    return result
