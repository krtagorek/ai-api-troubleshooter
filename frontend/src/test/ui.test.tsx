import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, it, expect, beforeEach, afterEach } from 'vitest'
import { Root } from '@/app'
import { Timeline } from '@/components/timeline'
import { ReportView } from '@/components/report'
import type { Report } from '@/lib/types'

beforeEach(() => { window.history.replaceState({}, '', '/'); vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => Promise.resolve(new Response(JSON.stringify(url.includes('health') ? { status: 'ready' } : url.includes('registry') ? [] : { items: [], offset: 0, limit: 20 }), { headers: { 'Content-Type': 'application/json' } })))) })
afterEach(() => vi.unstubAllGlobals())
it('shows a real empty workspace without fabricated incidents', async () => {
  render(<Root />); expect(screen.getByRole('heading', { name: 'Your diagnostic workspace' })).toBeVisible()
  expect(screen.getByText('Connect to see your incidents')).toBeVisible(); expect(screen.queryByText('Test diagnosis')).not.toBeInTheDocument()
  expect(await screen.findByText('API ready')).toBeVisible()
})
it('connects with an in-memory credential and clears cache on disconnect', async () => {
  const user = userEvent.setup(); render(<Root />)
  await user.click(screen.getAllByRole('button', { name: 'Connect API' })[0]); await user.type(screen.getByLabelText('Bearer token'), 'credential-canary')
  await user.click(screen.getByRole('button', { name: 'Connect securely' })); await user.click(await screen.findByRole('button', { name: 'Disconnect' }))
  expect(await screen.findByText('Connect to see your incidents')).toBeVisible()
  expect(window.localStorage.length).toBe(0); expect(window.sessionStorage.length).toBe(0)
  expect(screen.queryByDisplayValue('credential-canary')).not.toBeInTheDocument()
})
it('does not claim that a completed node means validation passed', () => {
  render(<Timeline events={[{ id: '00000000-0000-4000-8000-000000000001:1', event: 'node_completed', timestamp: '2026-10-22T10:00:00Z', data: { status: 'running', attempt: 1, node: 'validator' } }]} />)
  expect(screen.getByLabelText('Workflow wrapper completed')).toBeInTheDocument(); expect(screen.queryByText('Validation passed')).not.toBeInTheDocument()
})
it('renders report text as inert content and preserves NOT_TESTED', () => {
  const report: Report = { incident_id: 'one', summary: '<script>alert(1)</script>', diagnosis: { status: 'inconclusive', summary: 'Need evidence', confidence: 0, uncertainty: ['Missing trace'] }, evidence: { items: [], completeness: 'missing' }, validation_results: [{ status: 'NOT_TESTED', retry_recommended: false, issues: ['No tools configured'] }] }
  const { container } = render(<ReportView report={report} />)
  expect(screen.getByText('<script>alert(1)</script>')).toBeVisible(); expect(container.querySelector('script')).toBeNull()
  expect(screen.getByText('NOT TESTED')).toBeVisible(); expect(screen.getByText('Missing trace')).toBeVisible()
})
it('does not invent a conversation after opening the troubleshooter', async () => {
  window.history.replaceState({}, '', '/troubleshooter'); render(<Root />)
  expect(screen.getByRole('heading', { name: 'Let’s investigate.' })).toBeVisible()
  expect(screen.queryByRole('textbox', { name: 'What went wrong?' })).not.toBeInTheDocument()
  expect(screen.getByText('Your workspace, connected')).toBeVisible()
})
it('surfaces denied connections without reflecting server secrets', async () => {
  vi.mocked(fetch).mockResolvedValue(new Response('{"detail":"password=private"}', { status: 401 }))
  const user = userEvent.setup(); render(<Root />); await user.click(screen.getAllByRole('button', { name: 'Connect API' })[0]); await user.type(screen.getByLabelText('Bearer token'), 'bad-token'); await user.click(screen.getByRole('button', { name: 'Connect securely' }))
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('session is not authorized')); expect(document.body.textContent).not.toContain('password=private')
})
