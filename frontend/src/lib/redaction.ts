/** Convenience redaction before submission; server redaction remains authoritative. */
export function redactEvidence(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactEvidence)
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).filter(([key]) => !['root_cause', 'expected_resolution'].includes(key)).map(([key, item]) => [key, /password|passwd|secret|token|authorization|cookie|api[_-]?key|credential|email|phone|ssn/i.test(key) ? '[REDACTED]' : redactEvidence(item)]))
  if (typeof value !== 'string') return value
  return value.replace(/\b(?:Bearer|Basic)\s+[\w.+/=-]+/gi, '[REDACTED]').replace(/\beyJ[\w-]+\.[\w-]+\.[\w-]+/g, '[REDACTED]').replace(/\b(?:sk|pk|rk)-[\w-]+/g, '[REDACTED]').replace(/((?:password|secret|token|api[_-]?key)\s*[=:]\s*)[^\s,;]+/gi, '$1[REDACTED]').replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, '[REDACTED]')
}
