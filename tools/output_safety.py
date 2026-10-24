"""Trusted diagnostic-output boundary; operates on structures, never JSON text.

Policy v1: replace an unsafe generated request in its entirety with a marked,
nonsecret evidence request. Literal source observations/IDs are never rewritten.
Reports containing unsafe instruction-like source observations fail closed.
Provider audit records are deliberately outside this presentation boundary.
"""
from collections.abc import Mapping
import html
import json
import re
import unicodedata
from urllib.parse import unquote

POLICY_VERSION = 'diagnostic-output-safety-1'
NOTICE = 'Unsafe credential-material request replaced by trusted safety policy; confirmation remains outstanding.'
SAFE_REQUEST = ('[OUTPUT SAFETY: credential-material request withheld.] '
                'Ask the responsible owner for sanitized issuer, audience and expiry claims, '
                'verifier decisions and rejection reasons only. Exclude raw access tokens, refresh tokens, '
                'complete JWTs, passwords, API keys, session cookies and reusable credentials. '
                'No automatic collection or execution; obtain human approval.')
POLICY = ('Request only sanitized nonsecret issuer/audience/expiry claims, verifier decisions and rejection reasons. '
          'Never request raw access tokens, refresh tokens, complete JWTs, passwords, API keys, session cookies '
          'or reusable credentials. Never collect or execute anything automatically.')

MATERIAL = re.compile(r'\b(?:passwords?|passphrases?|passwd|api\s*keys?|session\s*(?:cookies?|ids?|identifiers?)|'
                      r'(?:refresh|access|bearer|raw|complete|full|encoded|signed)\s*tokens?|'
                      r'(?:raw|complete|full|encoded)\s*(?:jwt|json web token)s?|'
                      r'client\s*secrets?|reusable\s*credentials?|credentials?|'
                      r'authorization\s*(?:headers?|values?)|cookie\s*(?:headers?|values?)|'
                      r'login\s*(?:secrets?|details)|authentication\s*(?:secrets?|material|headers?)|'
                      r'secret\s*(?:keys?|values?)|bearer\s*(?:strings?|headers?)|cookies?)\b')
TOKEN = re.compile(r'\b(?:tokens?|jwt|json web token)s?\b')
ACTION = re.compile(r'\b(?:request|ask|need|provide|share|send|collect|retrieve|obtain|inspect|decode|'
                    r'analy[sz]e|upload|paste|copy|supply|include|attach|forward|export|disclose|'
                    r'hand over|access to|would (?:be )?(?:help|helpful|useful)|must have|require[ds]?|should see|await|awaiting)\b')
INDIRECT = re.compile(r'\b(?:evidence of|details (?:of|about)|copy of|sample of|missing|lack|'
                      r'not (?:provided|available)|unavailable|absent|needed|necessary|validation material)\b')
NEGATIVE = re.compile(r'^(?:please\s+)?(?:do not|don.t|never|exclude|without|must not|no collection of|no raw)\b')
ACTION_FIELDS = frozenset({'required_to_resolve','diagnostic_checks','steps','prerequisites',
                          'verification_steps','pending_user_questions','instructions','follow_up'})
LITERAL_FIELDS = frozenset({'evidence_id','evidence_ids','reference','source','observation',
    'incident_id','thread_id','hypothesis_id','selected_hypothesis_id','supporting_evidence_ids',
    'contradicting_evidence_ids','discriminating_evidence_ids','evidence_references',
    'evidence_contract','evidence_snapshot','selected_evidence_handles','sanitized_inputs','incident'})


class OutputSafetyError(ValueError):
    pass


def normalized(text):
    text = unicodedata.normalize('NFKC', html.unescape(unquote(text))).casefold()
    text = ''.join(c for c in text if unicodedata.category(c) != 'Cf')
    return re.sub(r'\s+', ' ', re.sub(r'[_\-]+', ' ', text.replace('\n',';'))).strip()


def _request_actions(clause):
    """Mask only bounded HTTP-request noun constructions, never solicitation verbs.

    This does not exempt a sentence: 'send the rejected HTTP request containing
    a JWT' still contains 'send' and must be rejected.
    """
    clause = re.sub(r'\b(?:http|api) requests?\b', 'http exchange', clause)
    clause = re.sub(r'\b(?:the|a|an|this|that) request(?= (?:failed|fails|returned|returns|was rejected|is rejected)\b)',
                    'the exchange', clause)
    return re.sub(r'\b(rejected|processed|handled) (the|a|an|this|that) request\b',
                  r'\1 \2 exchange', clause)


def _metadata_targets(clause):
    # Only an explicitly sanitized metadata object is allowed. Qualifiers in
    # another part of the sentence cannot authorize 'plus the complete JWT'.
    return re.sub(r'\b(?:sanitized|redacted|nonsecret|non secret) '
                  r'(?:(?:authentication|access) )?(?:token|jwt|json web token) '
                  r'(?:metadata|claims|issuer|audience|expiry|expiration)\b',
                  'sanitized metadata', clause)


def unsafe_request(text, *, request_context=False):
    if text in (SAFE_REQUEST, NOTICE):
        return False
    canonical=normalized(text)
    if re.search(r'\b(?:unless|except|provided that|until)\b',canonical) and (MATERIAL.search(canonical) or TOKEN.search(canonical)):
        return True  # Conditional permission cannot override the credential ban.
    if text.lstrip().startswith(('{','[','"')):
        try:
            decoded=json.loads(text)
        except (ValueError,RecursionError):
            decoded=None
        if isinstance(decoded,(dict,list)):
            def contains(value):
                if isinstance(value,dict):return any(contains(v) for v in value.values())
                if isinstance(value,list):return any(contains(v) for v in value)
                return isinstance(value,str) and unsafe_request(value,request_context=request_context)
            return contains(decoded)
        elif isinstance(decoded,str) and decoded!=text:
            return unsafe_request(decoded,request_context=request_context)
    requesting = request_context
    # Negation has local scope. Conditional exceptions never authorize secrets.
    for clause in re.split(r'([.;!?\n])|\b(?:but|and|however|unless|except)\b', canonical):
        if not clause:
            continue
        if clause in ('.',';','!','?','\n'):
            requesting = request_context
            continue
        clause = clause.strip()
        # Only a narrow trailing exclusion is safe. Earlier material requests
        # remain visible; an arbitrary negation never exempts the whole field.
        clause = re.sub(r'\s+without\s+(?:sensitive\s+|reusable\s+|raw\s+)?'
                        r'(?:credentials?|passwords?|tokens?|api\s*keys?|session\s*cookies?)$', '', clause)
        if NEGATIVE.search(clause):
            remainder=NEGATIVE.sub('',clause,count=1).strip()
            remainder=re.sub(r'^(?:request|ask|provide|share|send|collect|retrieve|obtain|upload|disclose)\b','',remainder,count=1)
            if ACTION.search(remainder) and (MATERIAL.search(remainder) or TOKEN.search(remainder)):
                return True
            requesting = False
            continue
        requesting = requesting or bool(ACTION.search(_request_actions(clause)) or INDIRECT.search(clause))
        if not requesting:
            continue
        targets = _metadata_targets(clause)
        if MATERIAL.search(targets):
            return True
        if TOKEN.search(targets):
            return True
    return False


def safe_text(text, *, request_context=False):
    return SAFE_REQUEST if unsafe_request(text, request_context=request_context) else text


def protect_generated(value, *, field='', gap=False):
    """No key deletion, scalar conversion or reference/observation substitution."""
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    if isinstance(value, Mapping):
        return {key: (True if key=='requires_human_approval' else item if key in LITERAL_FIELDS else protect_generated(
            item, field=key, gap=gap or key in ('missing','missing_evidence','diagnostic_gaps')))
                for key,item in value.items()}
    if isinstance(value, (list,tuple)):
        return [protect_generated(item, field=field, gap=gap) for item in value]
    if isinstance(value,str):
        return safe_text(value, request_context=gap or field in ACTION_FIELDS)
    return value


def protect_report(report):
    """Strict model validation precedes transformation; storage is never edited."""
    from models.diagnosis import FinalReport
    parsed = FinalReport.model_validate(report)
    for item in parsed.evidence.items:
        if unsafe_request(item.observation):
            raise OutputSafetyError('Unsafe instruction-like quoted evidence cannot be published; review required')
    return FinalReport.model_validate(protect_generated(parsed))


def protect_incident_view(incident):
    """Reject actionable unsafe source text; never rewrite its snapshot/grounding.

    Literal failure facts such as 'token missing' are not user-action requests.
    This is a publication check, not permission to follow source instructions.
    """
    from models.incident import Incident
    parsed = Incident.model_validate(incident)
    def check(value):
        if isinstance(value,dict):
            for key,item in value.items():
                check(key)
                check(item)
        elif isinstance(value,list):
            for item in value:check(item)
        elif isinstance(value,str):
            text=normalized(value)
            explicit=ACTION.search(text) or re.search(r'\b(?:evidence of|details of|sample of|copy of)\b',text)
            if explicit and unsafe_request(value):
                raise OutputSafetyError('Incident source requires safety review before publication')
    check(parsed.model_dump(mode='json'))
    return parsed


def progress_view(payload, *, status, event, attempt):
    """Progress is controlled metadata only; never publish arbitrary old payloads."""
    nodes={'initialize','classifier','log_investigator','root_cause_analyst',
           'resolution_generator','validator','collect_evidence','request_user_input',
           'retry_analysis','validation_exhausted','final_report'}
    statuses={'queued','running','succeeded','awaiting_evidence','interrupted','failed','cancelled'}
    if status not in statuses:
        raise OutputSafetyError('Invalid progress status')
    result={'status':status,'attempt':attempt}
    if isinstance(payload,dict) and payload.get('status') in statuses:
        result['status']=payload['status']
    if isinstance(payload,dict) and type(payload.get('attempt')) is int and payload['attempt']>=0:
        result['attempt']=payload['attempt']
    if event=='node_completed':
        if not isinstance(payload,dict) or payload.get('node') not in nodes:
            raise OutputSafetyError('Invalid progress node')
        result['node']=payload['node']
    return result
