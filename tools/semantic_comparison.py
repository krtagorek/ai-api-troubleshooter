"""Bounded same-scope comparisons of catalog facts; never causal confirmation."""
import math
import re

# A deliberately small vocabulary. No implicit conversion or inferred units.
METRICS = {
    'scale': None, 'count': None, 'requests': None, 'utilization': None,
    'latency': 'time', 'duration': 'time', 'elapsed': 'time',
    'age': 'time', 'size': 'bytes',
}
UNITS = {'ms': 'time', 'seconds': 'time', 'bytes': 'bytes'}
OBSERVED = {'received', 'actual', 'observed', 'measured'}
UPPER = {'allowed', 'max', 'maximum', 'limit', 'upper'}
LOWER = {'min', 'minimum', 'lower'}


def numeric_role(name):
    words = name.split('_')
    unit = words.pop() if words[-1] in UNITS else None
    observed = [w for w in words if w in OBSERVED]
    upper = [w for w in words if w in UPPER]
    lower = [w for w in words if w in LOWER]
    # Configured alone has no direction; threshold alone has no direction.
    if sum(bool(x) for x in (observed, upper, lower)) != 1:
        return None
    role = 'observed' if observed else 'upper' if upper else 'lower'
    role_words = OBSERVED | UPPER | LOWER | {'configured', 'threshold', 'bound'}
    metric = '_'.join(w for w in words if w not in role_words)
    # Preserve the entire namespace in the identity: cache_age and token_age
    # are different metrics even though both have a time dimension.
    base = metric.split('_')[-1]
    if base not in METRICS:
        return None
    dimension = METRICS[base]
    if (dimension is None and unit is not None) or (dimension is not None and UNITS.get(unit) != dimension):
        return None
    return metric, unit, role


def numeric(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def threshold_pair(left, right):
    """Return direction only for exactly the same metric and unit."""
    a, b = numeric_role(left), numeric_role(right)
    if a and b and a[:2] == b[:2] and a[2] == 'observed' and b[2] in ('upper', 'lower'):
        return b[2]
    # Historical deadline/budget names: explicit bounded semantic relations,
    # rather than comparing every pair that happens to share units.
    special = {
        'observed_latency_ms': {'request_deadline_ms', 'deadline_ms', 'budget_ms'},
        'observed_age_seconds': {'max_age_seconds'},
        'observed_size_bytes': {'min_size_bytes'},
        # A partitioned request counter's explicitly named limit, and a DNS
        # phase exceeding the enclosing request's entire deadline.
        'bucket_requests': {'bucket_limit'},
        'dns_elapsed_ms': {'request_deadline_ms'},
    }
    if right in special.get(left, set()):
        return 'lower' if right.startswith('min_') else 'upper'
    return None


def comparison(facts):
    for name, values in facts.items():
        for limit, thresholds in facts.items():
            direction = threshold_pair(name, limit)
            if not direction:
                continue
            for a, ar in values:
                for b, br in thresholds:
                    if numeric(a) and numeric(b) and (a > b if direction == 'upper' else a < b):
                        return ar, br
    return None


def conflicting_bounds(facts):
    groups = {}
    for name, values in facts.items():
        role = numeric_role(name)
        if role and role[2] in ('upper', 'lower'):
            for value, _ in values:
                if numeric(value):
                    groups.setdefault(role[:2], {}).setdefault(role[2], set()).add(value)
    for bounds in groups.values():
        if any(len(values) > 1 for values in bounds.values()):
            return True
        if bounds.get('lower') and bounds.get('upper') and max(bounds['lower']) > min(bounds['upper']):
            return True
    return False


QUALITY_WARNING = re.compile(
    r'\b(?:diagnostic\s+sampling\s+incomplete|(?:sampling|collection|telemetry|diagnostics)'
    r'\s+(?:is\s+)?(?:incomplete|partial|unavailable)|(?:logs|events)\s+(?:were\s+)?dropped)\b', re.I)


def quality_limitation(entry):
    return entry.reference.startswith('/logs/') and bool(QUALITY_WARNING.search(entry.observation))
