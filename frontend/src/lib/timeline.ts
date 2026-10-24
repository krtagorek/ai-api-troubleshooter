import type { ProgressEvent } from './sse'

/** Persisted node_completed proves wrapper completion only. It is not a model
 * invocation receipt, authorization verdict, or validation result. No report or
 * terminal job status is used to fill missing node history. */
export function reconstructTimeline(events: ProgressEvent[]) {
  const rows = new Map<string, ProgressEvent>(); const conflicts = new Set<string>(); const jobs = new Set<string>()
  let incomplete = false
  for (const event of events) {
    const match = /^([a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}):([1-9]\d*)$/i.exec(event.id)
    if (!match || !Number.isSafeInteger(Number(match[2])) || !Number.isInteger(event.data.attempt) || event.data.attempt < 0 || event.data.attempt > 5) { incomplete = true; continue }
    jobs.add(match[1].toLowerCase())
    const prior = rows.get(event.id)
    if (prior && JSON.stringify([prior.event, prior.data.node, prior.data.attempt, prior.data.status, prior.timestamp]) !== JSON.stringify([event.event, event.data.node, event.data.attempt, event.data.status, event.timestamp])) { conflicts.add(event.id); incomplete = true }
    rows.set(event.id, event)
  }
  // Never combine completion receipts from different jobs.
  if (jobs.size > 1) return { completed: new Map<string, number[]>(), attempts: [], incomplete: true }
  const ordered = [...rows.values()].filter(e => !conflicts.has(e.id)).sort((a, b) => Number(a.id.split(':')[1]) - Number(b.id.split(':')[1]))
  const completed = new Map<string, number[]>()
  const attempts = [...new Set(ordered.map(e => e.data.attempt).filter(a => a > 0))].sort((a, b) => a - b)
  let expected = 1
  for (const event of ordered) {
    const sequence = Number(event.id.split(':')[1]); if (sequence !== expected) incomplete = true
    expected = sequence + 1
    if (event.event === 'node_completed' && event.data.node && event.data.attempt > 0) {
      completed.set(event.data.node, [...new Set([...(completed.get(event.data.node) || []), event.data.attempt])].sort((a, b) => a - b))
    }
  }
  return { completed, attempts, incomplete }
}
