import { render, screen, cleanup } from '@testing-library/react'
import { expect, it, afterEach } from 'vitest'
import { Timeline } from './timeline'
import fixtures from '../test/fixtures/crash-progress.json'
import type { ProgressEvent } from '@/lib/sse'
import { reconstructTimeline } from '@/lib/timeline'
afterEach(cleanup)
const rows = (name: keyof typeof fixtures) => fixtures[name] as ProgressEvent[]
it.each(['claim', 'investigator', 'semantic', 'publication'] as const)('retains every wrapper completion: %s', name => {
  render(<Timeline events={rows(name)} />)
  expect(screen.getAllByLabelText('Workflow wrapper completed')).toHaveLength(5)
  expect(screen.getAllByText('Model invocation: not recorded')).toHaveLength(5)
})
it.each(['contract_revocation', 'endpoint_revocation', 'producer_revocation'] as const)('does not claim agent execution from denied wrappers: %s', name => {
  render(<Timeline events={rows(name)} />)
  expect(screen.getAllByText('Model invocation: not recorded')).toHaveLength(5)
  expect(screen.getByText(/Denied, skipped, or interrupted/)).toBeVisible()
})
it('preserves attempt provenance and overlapping completions', () => {
  const source = rows('investigator'); const classifier = source.find(e => e.data.node === 'classifier')!
  const repeated = { ...classifier, id: classifier.id.split(':')[0] + ':12', data: { ...classifier.data, attempt: 2 } }
  const result = reconstructTimeline([...source, repeated])
  expect(result.completed.get('classifier')).toEqual([1, 2])
  expect(result.attempts).toEqual([1, 2])
})
it('sorts out-of-order events and deduplicates replay', () => {
  const source = rows('investigator')
  expect(reconstructTimeline([...source].reverse().concat(source))).toEqual(reconstructTimeline(source))
})
it('keeps known completions but warns about missing events', () => {
  const result = reconstructTimeline(rows('investigator').filter(e => e.data.node !== 'classifier'))
  expect(result.incomplete).toBe(true); expect(result.completed.has('classifier')).toBe(false)
  expect(result.completed.has('log_investigator')).toBe(true)
})
it('does not infer node completion from terminal success', () => {
  render(<Timeline events={rows('publication').filter(e => e.event === 'succeeded')} />)
  expect(screen.queryAllByLabelText('Workflow wrapper completed')).toHaveLength(0)
  expect(screen.getAllByText('No wrapper completion recorded')).toHaveLength(5)
})
it('rejects conflicting receipts and cross-job history', () => {
  const source = rows('investigator'); const item = source.find(e => e.data.node === 'classifier')!
  expect(reconstructTimeline([...source, { ...item, data: { ...item.data, node: 'validator' } }]).completed.has('classifier')).toBe(false)
  expect(reconstructTimeline([...source, ...rows('publication')]).completed.size).toBe(0)
})
it('shows unknown history for invalid event identifiers', () => {
  expect(reconstructTimeline([{ ...rows('claim')[0], id: 'fabricated' }]).incomplete).toBe(true)
})
