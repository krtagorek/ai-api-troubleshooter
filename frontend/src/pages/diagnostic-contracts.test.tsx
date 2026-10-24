import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { Root } from '@/app'
import { publicObject, validSelector } from './diagnostic-contracts'
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState({}, '', '/') })
describe('public administration metadata', () => {
  it('allows a public signature and key reference without altering signed bytes', () => {
    const value = { algorithm: 'Ed25519', key_id: 'test-key', signature: 'A'.repeat(88), payload: { snapshot: { logs: [] } } }
    expect(publicObject(JSON.stringify(value))).toEqual(value)
  })
  it.each(['{"private_key":"x"}', '{"nested":{"password":"x"}}', '{"access_token":"x"}', '{"api_key":"x"}', '{"value":"Basic dXNlcjpwYXNz"}', '{"value":"password=abc"}', '{"value":"Bearer reusable"}', '{"value":"-----BEGIN PRIVATE KEY-----"}', '[]'])('rejects unsafe or nonobject metadata: %s', text => expect(() => publicObject(text)).toThrow())
  it('rejects oversized content', () => expect(() => publicObject('{"x":"' + 'x'.repeat(240000) + '"}')).toThrow())
})
it('shows unavailable administration safely and never displays provider error contents', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url.includes('/diagnostic-admin/') ? { detail: 'private_key=must-not-render' } : url.endsWith('/health/ready') ? { status: 'ready' } : { items: [] }), { status: url.includes('/diagnostic-admin/') ? 503 : 200, headers: { 'Content-Type': 'application/json' } })))
  window.history.replaceState({}, '', '/diagnostic-contracts'); render(<Root />)
  fireEvent.click(screen.getAllByRole('button', { name: /^Connect API$/ })[0])
  fireEvent.change(screen.getByLabelText('Bearer token'), { target: { value: 'synthetic-session' } });fireEvent.click(screen.getByRole('button', { name: 'Connect securely' }))
  await screen.findByRole('button', { name: 'Disconnect' })
  fireEvent.change(screen.getByLabelText('Tenant ID'), { target: { value: '11111111-1111-4111-8111-111111111111' } });fireEvent.click(screen.getByRole('button', { name: 'Load tenant' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('unavailable')
  expect(screen.queryByText('private_key=must-not-render')).not.toBeInTheDocument()
})

it('rejects non-UUID selectors before constructing administration request URLs', () => { expect(validSelector('11111111-1111-4111-8111-111111111111')).toBe(true); expect(validSelector('Bearer reusable-credential')).toBe(false); expect(validSelector('https://external.invalid')).toBe(false) })
