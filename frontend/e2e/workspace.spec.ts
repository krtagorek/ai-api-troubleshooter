import crashProgress from '../src/test/fixtures/crash-progress.json' with { type: 'json' }
import { test, expect, type Page } from '@playwright/test'
const iid = '11111111-1111-4111-a111-111111111111', inv = '22222222-2222-4222-a222-222222222222', job = '33333333-3333-4333-a333-333333333333'
const incident = { incident_id: iid, service: 'Inventory', endpoint: '/inventory/1', http_method: 'GET', status_code: 401, severity: 'medium', request_metadata: {}, response_body: { error: 'Token missing' }, logs: [], expected_behavior: 'Return inventory', actual_behavior: 'Inventory rejects authentication', evidence_quality: 'missing' }
const detail = (status = 'pending') => ({ incident, investigations: [{ investigation_id: inv, thread_id: job, status }] })
async function defaults(page: Page) {
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/health/ready')) return route.fulfill({ json: { status: 'ready' } })
    if (path.endsWith('/registry')) return route.fulfill({ json: [] })
    if (path.endsWith('/incidents')) return route.fulfill({ json: { items: [], limit: 20, offset: 0 } })
    return route.fulfill({ status: 404, json: { detail: 'Unavailable' } })
  })
}
async function connect(page: Page) {
  await page.getByRole('button', { name: 'Connect API', exact: true }).first().click()
  await page.getByLabel('Bearer token').fill('test-browser-token')
  await page.getByRole('button', { name: 'Connect securely' }).click()
  await expect(page.getByRole('button', { name: 'Disconnect' })).toBeVisible()
}
test('desktop dashboard has truthful empty states and no browser errors', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message)); await defaults(page)
  await page.setViewportSize({ width: 1440, height: 1060 }); await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Your diagnostic workspace' })).toBeVisible()
  await expect(page.getByText('API ready', { exact: true })).toBeVisible()
  await expect(page.getByText('Connect to see your incidents')).toBeVisible()
  await page.screenshot({ path: 'test-results/dashboard-desktop.png', fullPage: true }); expect(errors).toEqual([])
})
test('mobile navigation reaches all pages without document overflow', async ({ page }) => {
  await defaults(page); await page.setViewportSize({ width: 390, height: 844 }); await page.goto('/')
  for (const [link, title] of [['AI Troubleshooter', 'Let’s investigate.'], ['Incident history', 'Every investigation, in context.'], ['API Registry', 'A trusted perimeter for your APIs.'], ['Admin approvals', 'Approval starts with evidence.'], ['Dashboard', 'Your diagnostic workspace']]) {
    await page.getByRole('button', { name: 'Toggle navigation' }).click(); await page.getByRole('navigation').getByRole('link', { name: link, exact: true }).click()
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  }
  await expect.poll(() => page.locator('aside').evaluate(el => el.getBoundingClientRect().right)).toBeLessThanOrEqual(0)
  await page.screenshot({ path: 'test-results/dashboard-mobile.png', fullPage: true, animations: 'disabled' })
})
test('creates sanitized incident and reports a 503 without inventing an accepted job', async ({ page }) => {
  await defaults(page); let submitted: unknown
  await page.route('**/api/v1/incidents', route => { submitted = route.request().postDataJSON(); return route.fulfill({ status: 201, json: detail() }) })
  await page.route(`**/api/v1/incidents/${iid}`, route => route.fulfill({ json: detail() }))
  await page.route(`**/api/v1/incidents/${iid}/report`, route => route.fulfill({ status: 409, json: {} }))
  await page.route(`**/api/v1/investigations/${inv}/execute`, route => route.fulfill({ status: 503, json: { detail: 'No runner' } }))
  await page.goto('/troubleshooter'); await connect(page)
  await page.getByLabel('What went wrong?').fill('Inventory rejects authentication')
  await page.getByLabel('Service', { exact: true }).fill('Inventory'); await page.getByLabel('Endpoint path').fill('/inventory/1')
  await page.getByLabel('HTTP status').fill('401'); await page.getByLabel('Expected behavior').fill('Return inventory')
  await page.getByText('Attach sanitized evidence').click(); await page.getByLabel('Request metadata · JSON object').fill('{"Authorization":"Bearer browser-secret-canary"}')
  await page.getByRole('button', { name: 'Create investigation' }).click()
  await expect(page).toHaveURL(`/incidents/${iid}`); expect(JSON.stringify(submitted)).not.toContain('browser-secret-canary')
  await page.getByRole('button', { name: 'Submit to worker' }).click()
  await expect(page.getByRole('alert')).toContainText('Acceptance was not confirmed')
  await expect(page.getByText('Progress starts after a job is accepted.')).toBeVisible(); await expect(page.getByText('The report will appear here')).toBeVisible()
})
test('renders ordered SSE and persisted report without rendering hostile HTML', async ({ page }) => {
  await defaults(page)
  await page.route(`**/api/v1/incidents/${iid}`, route => route.fulfill({ json: detail('completed') }))
  await page.route(`**/api/v1/incidents/${iid}/report`, route => route.fulfill({ json: { incident_id: iid, summary: '<img src=x onerror=alert(1)>', diagnosis: { status: 'supported', confidence: .8, summary: 'Missing authentication', selected_hypothesis_id: 'h1' }, evidence: { items: [], completeness: 'complete' }, validation_results: [{ status: 'NOT_TESTED', retry_recommended: false, issues: ['No validation tools configured'] }] } }))
  await page.route(`**/api/v1/investigations/${inv}/events`, route => route.fulfill({ contentType: 'text/event-stream', body: [{ event: 'node_completed', status: 'running', node: 'classifier' }, { event: 'succeeded', status: 'succeeded', node: 'final_report' }].map((e, i) => `id: ${job}:${i + 1}\nevent: ${e.event}\ndata: ${JSON.stringify({ data: { status: e.status, attempt: 1, node: e.node }, timestamp: '2026-10-22T12:00:00Z' })}\n\n`).join('') }))
  await page.goto(`/incidents/${iid}`); await connect(page)
  await expect(page.getByText('Stream: Complete')).toBeVisible(); await expect(page.getByLabel('Persisted diagnostic report')).toBeVisible()
  await expect(page.getByText('NOT TESTED', { exact: true })).toBeVisible(); await expect(page.locator('main img')).toHaveCount(0)
  await expect(page.getByText('<img src=x onerror=alert(1)>')).toBeVisible()
})
test('registry actions remain server-authorized and denied approval is not displayed as success', async ({ page }) => {
  await defaults(page)
  const registration = { api_id: iid, owner_id: inv, policy: { origin: 'https://api.example.com', kind: 'external', operations: [{ operation_id: 'health', method: 'GET', path: '/health', demo_auth: false }] }, pinned_ips: ['93.184.216.34'], status: 'verified', revision: 1, verified_revision: 1, approved_revision: null, approval_digest: null, challenge_expires_at: '2026-10-22T12:00:00Z' }
  await page.route('**/api/v1/registry?*', route => route.fulfill({ json: [registration] }))
  await page.route(`**/api/v1/registry/${iid}/approve`, route => route.fulfill({ status: 403, json: { detail: 'Administrator required' } }))
  await page.goto('/approvals'); await connect(page)
  await page.getByRole('button', { name: 'Approve', exact: true }).click(); await page.getByRole('button', { name: 'Confirm approve' }).click()
  await expect(page.getByRole('alert')).toContainText('do not have permission')
  await expect(page.locator('tbody').getByText('Approved', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Close dialog' }).click(); await expect(page.getByText('Verified', { exact: true }).last()).toBeVisible()
})
test('registry registration sends exact policy and clears one-time challenge on disconnect', async ({ page }) => {
  await defaults(page)
  await page.route('**/api/v1/registry', route => route.fulfill({ status: 201, json: { registration: { api_id: iid, owner_id: inv, status: 'pending', revision: 1, policy: route.request().postDataJSON(), pinned_ips: ['93.184.216.34'], challenge_expires_at: '2026-10-22T12:00:00Z' }, verification_challenge: 'one-time-challenge-canary' } }))
  await page.goto('/registry'); await connect(page); await page.getByRole('button', { name: 'Register API', exact: true }).click()
  await page.getByLabel('Canonical origin').fill('https://api.example.com'); await page.getByRole('button', { name: 'Save pending policy' }).click()
  await expect(page.getByLabel('Ownership challenge')).toHaveValue('one-time-challenge-canary')
  // Closing removes the one-time value rather than persisting it in browser storage.
  await page.getByRole('button', { name: 'Close dialog' }).click(); await page.getByRole('button', { name: 'Disconnect' }).click()
  expect(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]))).not.toContain('one-time-challenge-canary')
  await expect(page.getByLabel('Ownership challenge')).toHaveCount(0)
})

test('authoritative completed state wins over replayed Running and report NOT_TESTED remains explicit', async ({ page }) => {
  await defaults(page)
  await page.route(`**/api/v1/incidents/${iid}`, route => route.fulfill({ json: detail('completed') }))
  await page.route(`**/api/v1/incidents/${iid}/report`, route => route.fulfill({ json: { incident_id: iid, summary: 'Persisted review only', diagnosis: { status: 'inconclusive', confidence: 0, summary: 'Need more evidence' }, evidence: { items: [], completeness: 'missing' }, validation_results: [{ status: 'NOT_TESTED', retry_recommended: false }] } }))
  const progress = (sequence: number, event: string, status: string) => `id: ${job}:${sequence}\nevent: ${event}\ndata: ${JSON.stringify({ data: { status, attempt: 1, node: 'classifier' }, timestamp: '2026-10-22T12:00:00Z' })}\n\n`
  await page.route(`**/api/v1/investigations/${inv}/events`, route => route.fulfill({ contentType: 'text/event-stream', body: progress(1, 'node_completed', 'running') + progress(1, 'node_completed', 'running') + progress(2, 'succeeded', 'succeeded') + progress(3, 'node_completed', 'running') }))
  await page.goto(`/incidents/${iid}`); await connect(page)
  await expect(page.getByText('Completed', { exact: true })).toBeVisible()
  await expect(page.getByText('NOT TESTED', { exact: true })).toBeVisible()
  await expect(page.getByText('Stream: Complete')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Request cancellation' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Run activity', exact: true }).locator('..').getByRole('list').locator('li')).toHaveCount(2)
})

test('refresh during and after execution rebuilds persisted timeline without submitting another job', async ({ page }) => {
  await defaults(page); let complete = false; let executionRequests = 0
  await page.route(`**/api/v1/incidents/${iid}`, route => route.fulfill({ json: detail(complete ? 'completed' : 'running') }))
  await page.route(`**/api/v1/incidents/${iid}/report`, route => route.fulfill({ status: 409, json: {} }))
  await page.route(`**/api/v1/investigations/${inv}/execute`, route => { executionRequests++; return route.fulfill({ status: 202, json: { job_id: job, status: 'accepted' } }) })
  await page.route(`**/api/v1/investigations/${inv}/events`, route => {
    const nodes = complete ? ['classifier', 'log_investigator', 'root_cause_analyst', 'resolution_generator', 'validator'] : ['classifier']
    const body = nodes.map((node, i) => `id: ${job}:${i + 1}\nevent: node_completed\ndata: ${JSON.stringify({ data: { status: 'running', attempt: 1, node }, timestamp: '2026-10-22T12:00:00Z' })}\n\n`).join('')
    return route.fulfill({ contentType: 'text/event-stream', body: body + (complete ? `id: ${job}:6\nevent: succeeded\ndata: ${JSON.stringify({ data: { status: 'succeeded', attempt: 1 }, timestamp: '2026-10-22T12:00:00Z' })}\n\n` : '') })
  })
  await page.goto(`/incidents/${iid}`); await connect(page); await expect(page.getByLabel('Workflow wrapper completed')).toHaveCount(1)
  await page.reload(); await connect(page); await expect(page.getByLabel('Workflow wrapper completed')).toHaveCount(1)
  complete = true
  await page.reload(); await connect(page); await expect(page.getByLabel('Workflow wrapper completed')).toHaveCount(5)
  await expect(page.getByText('Completed', { exact: true })).toBeVisible()
  await expect(page.getByText('The report will appear here')).toBeVisible() // Completed is not proof that a report exists.
  await page.reload(); await connect(page); await expect(page.getByLabel('Workflow wrapper completed')).toHaveCount(5)
  expect(executionRequests).toBe(0)
})

test('expired SSE credentials stop automatic reconnect and never expose server error contents', async ({ page }) => {
  await defaults(page); let requests = 0
  await page.route(`**/api/v1/incidents/${iid}`, route => route.fulfill({ json: detail('running') }))
  await page.route(`**/api/v1/incidents/${iid}/report`, route => route.fulfill({ status: 409, json: {} }))
  await page.route(`**/api/v1/investigations/${inv}/events`, route => { requests++; return route.fulfill({ status: 401, json: { detail: 'password=must-not-render' } }) })
  await page.goto(`/incidents/${iid}`); await connect(page)
  await expect(page.getByRole('alert')).toContainText('session is not authorized')
  await expect(page.getByText('Stream: Error')).toBeVisible()
  expect(requests).toBe(1); await expect(page.getByText('password=must-not-render')).toHaveCount(0)
  await page.getByRole('navigation').getByRole('link', { name: 'Dashboard', exact: true }).click()
  expect(requests).toBe(1)
})

for (const [name, status, count] of [['investigator', 'completed', 5], ['contract_revocation', 'awaiting_evidence', 3]] as const) {
  test(`cross-attempt wrapper history is truthful after replay and refresh: ${name}`, async ({ page }) => {
    await defaults(page)
    await page.route(`**/api/v1/incidents/${iid}`, route => route.fulfill({ json: detail(status) }))
    await page.route(`**/api/v1/incidents/${iid}/report`, route => route.fulfill({ status: 409, json: {} }))
    const events = crashProgress[name]
    await page.route(`**/api/v1/investigations/${inv}/events`, route => route.fulfill({ contentType: 'text/event-stream', body: [events[0], ...events].map(e => `id: ${e.id}\nevent: ${e.event}\ndata: ${JSON.stringify({ data: e.data, timestamp: e.timestamp })}\n\n`).join('') }))
    await page.goto(`/incidents/${iid}`); await connect(page)
    await expect(page.getByLabel('Workflow wrapper completed')).toHaveCount(count)
    await expect(page.getByText('Model invocation: not recorded', { exact: true })).toHaveCount(5)
    await expect(page.getByText('Worker attempts: 1, 2. Prior wrapper completions are retained across recovery.', { exact: true })).toBeVisible()
    await page.reload(); await connect(page)
    await expect(page.getByLabel('Workflow wrapper completed')).toHaveCount(count)
    await expect(page.getByText('Model invocation: not recorded', { exact: true })).toHaveCount(5)
    await expect(page.getByLabel('Persisted diagnostic report')).toHaveCount(0)
    await expect(page.getByText('Stream: Complete')).toBeVisible()
  })
}
