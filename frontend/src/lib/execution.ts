import { terminalEvents, type ProgressEvent } from './sse'
export function executionTerminal(status?: string): boolean { return status === 'completed' || terminalEvents.has(status || '') }
/** Backend terminal projection wins; a persisted terminal event wins over an older active query. */
export function reconcileExecution(backend: string | undefined, events: ProgressEvent[]): string {
  if (executionTerminal(backend)) return backend!
  const terminal = [...events].reverse().find(event => terminalEvents.has(event.event))
  if (terminal) return terminal.event === 'succeeded' ? 'completed' : terminal.event
  return backend && backend !== 'pending' ? backend : events.at(-1)?.data.status || backend || 'pending'
}
