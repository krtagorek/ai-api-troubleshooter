import type { IncidentSubmission, IncidentDetail, IncidentPage, Report, Policy, Registration, Challenge, AuditEvent, Receipt } from './types'

const messages: Record<number, string> = {
  400: 'The request or event cursor was rejected. Reconnect from the beginning if needed.',
  401: 'Your session is not authorized. Reconnect with a valid bearer token.',
  403: 'You do not have permission for this action. Administrator authorization may be required.',
  404: 'This record is unavailable to your identity, or it does not exist.',
  409: 'The current state does not permit this action. Refresh and check the lifecycle requirements.',
  422: 'The submitted fields do not satisfy the API contract or security policy.',
  429: 'Too many requests. Wait before trying again.',
  503: 'The service or execution runner is unavailable. Acceptance was not confirmed; check status before retrying.',
}
export class ApiError extends Error {
  constructor(public status: number, public correlation?: string) { super(messages[status] ?? 'The request could not be completed. Try again after checking the service.'); this.name = 'ApiError' }
}
export const baseUrl = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')
export function validatedBase(value: string) {
  const url = new URL(value, window.location.origin)
  if (url.username || url.password || url.search || url.hash || !['http:', 'https:'].includes(url.protocol)) throw new Error('Invalid API origin configuration')
  if (url.protocol !== 'https:' && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) throw new Error('Remote API origins must use HTTPS')
  return value.replace(/\/$/, '')
}
export class Api {
  private base: string
  constructor(private token: string, base = baseUrl) { this.base = validatedBase(base) }
  async response(path: string, options: RequestInit = {}) {
    let response: Response
    try {
      response = await fetch(`${this.base}${path}`, { ...options, redirect: 'error', credentials: 'omit', cache: 'no-store', headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers, Authorization: `Bearer ${this.token}` } })
    } catch (error) {
      if (options.signal?.aborted) throw error
      throw new ApiError(0)
    }
    if (!response.ok) {
      const raw = response.headers.get('X-Correlation-ID') || ''
      throw new ApiError(response.status, /^[a-f\d-]{36}$/i.test(raw) ? raw : undefined)
    }
    return response
  }
  async request<T>(path: string, options: RequestInit = {}): Promise<T> { return (await this.response(path, options)).json() }
  incidents = (offset = 0, signal?: AbortSignal) => this.request<IncidentPage>(`/incidents?limit=20&offset=${offset}`, { signal })
  incident = (id: string, signal?: AbortSignal) => this.request<IncidentDetail>(`/incidents/${encodeURIComponent(id)}`, { signal })
  create = (value: IncidentSubmission) => this.request<IncidentDetail>('/incidents', { method: 'POST', body: JSON.stringify(value) })
  report = async (id: string, signal?: AbortSignal) => {
    try { return await this.request<Report>(`/incidents/${encodeURIComponent(id)}/report`, { signal }) }
    catch (error) { if (error instanceof ApiError && error.status === 409) return null; throw error }
  }
  execute = (id: string) => this.request<Receipt>(`/investigations/${encodeURIComponent(id)}/execute`, { method: 'POST' })
  cancel = (id: string) => this.request<{ job_id: string; status: string; cancel_requested: boolean }>(`/investigations/${encodeURIComponent(id)}/cancel`, { method: 'POST' })
  registry = (signal?: AbortSignal) => this.request<Registration[]>('/registry?limit=100', { signal })
  register = (policy: Policy) => this.request<Challenge>('/registry', { method: 'POST', body: JSON.stringify(policy) })
  mock = () => this.request<Challenge>('/registry/development-mock', { method: 'POST' })
  update = (id: string, policy: Policy) => this.request<Challenge>(`/registry/${encodeURIComponent(id)}/policy`, { method: 'PUT', body: JSON.stringify(policy) })
  verify = (id: string, challenge: string) => this.request<Registration>(`/registry/${encodeURIComponent(id)}/verify`, { method: 'POST', body: JSON.stringify({ challenge }) })
  transition = (id: string, action: 'approve' | 'suspend' | 'revoke') => this.request<Registration>(`/registry/${encodeURIComponent(id)}/${action}`, { method: 'POST' })
  history = (id: string, signal?: AbortSignal) => this.request<AuditEvent[]>(`/registry/${encodeURIComponent(id)}/history`, { signal })
}
