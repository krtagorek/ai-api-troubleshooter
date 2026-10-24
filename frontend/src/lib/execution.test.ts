import { it, expect } from 'vitest'
import { reconcileExecution } from './execution'
import { mergeProgress, type ProgressEvent } from './sse'
const event = (sequence: number, status: 'running' | 'succeeded' = 'running'): ProgressEvent => ({ id: `job:${sequence}`, event: status === 'succeeded' ? 'succeeded' : 'node_completed', data: { status, attempt: 1, node: 'classifier' }, timestamp: '2026-10-22T12:00:00Z' })
it.each(['completed', 'failed', 'cancelled', 'awaiting_evidence', 'interrupted'])('backend %s cannot be overwritten by stale Running events', status => { expect(reconcileExecution(status, [event(1)])).toBe(status) })
it('persisted terminal progress wins over an older active query response', () => { expect(reconcileExecution('running', [event(1), event(2, 'succeeded'), event(3)])).toBe('completed') })
it('deduplicates and orders UI history by persisted ID', () => { const first = event(1), second = event(2); const items = mergeProgress([second], first); expect(items.map(e => e.id)).toEqual(['job:1','job:2']); expect(mergeProgress(items, first)).toBe(items) })
it('report availability is not an input to execution status or validation', () => { expect(reconcileExecution('running', [])).toBe('running'); expect(reconcileExecution('completed', [])).toBe('completed') })
