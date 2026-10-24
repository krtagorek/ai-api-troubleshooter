import { it, expect, vi, afterEach } from 'vitest'
import { Api, ApiError, validatedBase } from './api'
import { redactEvidence } from './redaction'
afterEach(() => vi.unstubAllGlobals())
it('sends credentials only in headers and blocks redirects and cookie credentials', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], limit: 20, offset: 0 }))); vi.stubGlobal('fetch', fetcher)
  await new Api('sensitive-canary').incidents()
  expect(fetcher.mock.calls[0][0]).not.toContain('sensitive-canary')
  expect(fetcher.mock.calls[0][1]).toMatchObject({ redirect: 'error', credentials: 'omit', cache: 'no-store', headers: { Authorization: 'Bearer sensitive-canary' } })
})
it('does not reflect raw API errors or credentials', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"detail":"password=leak"}', { status: 422 })))
  await expect(new Api('secret').incidents()).rejects.toThrow('submitted fields')
})
it('maps report 409 to not available while preserving authentication errors', async () => {
  const get = vi.fn().mockResolvedValueOnce(new Response('', { status: 409 })).mockResolvedValueOnce(new Response('', { status: 401 })); vi.stubGlobal('fetch', get)
  const api = new Api('secret'); expect(await api.report('id')).toBeNull(); await expect(api.report('id')).rejects.toBeInstanceOf(ApiError)
})
it('uses fixed registry action routes', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response('{}')); vi.stubGlobal('fetch', fetcher)
  await new Api('secret').transition('some/id', 'approve')
  expect(fetcher.mock.calls[0][0]).toBe('/api/v1/registry/some%2Fid/approve'); expect(fetcher.mock.calls[0][1].method).toBe('POST')
})
it.each(['http://remote.example/api', 'https://user:password@example.com', 'javascript:alert(1)', 'https://api.example.com?token=x'])('rejects insecure API configuration %s', value => expect(() => validatedBase(value)).toThrow())
it('redacts recognized evidence secrets and removes evaluation labels', () => {
  const safe = JSON.stringify(redactEvidence({ Authorization: 'Bearer raw-secret', nested: { password: 'bad', email: 'person@example.com' }, root_cause: 'answer', expected_resolution: 'label', logs: ['Bearer raw-secret', 'sk-long-key'] }))
  for (const value of ['raw-secret', 'bad', 'person@example.com', 'answer', 'label', 'sk-long-key']) expect(safe).not.toContain(value)
})
