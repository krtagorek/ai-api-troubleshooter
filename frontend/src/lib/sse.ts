import { z } from 'zod'
import { Api, ApiError } from './api'

export const terminalEvents = new Set(['succeeded', 'awaiting_evidence', 'interrupted', 'failed', 'cancelled'])
const eventNames = z.enum(['queued', 'claimed', 'node_completed', 'retry_scheduled', 'cancel_requested', 'succeeded', 'awaiting_evidence', 'interrupted', 'failed', 'cancelled'])
const payloadSchema = z.object({ data: z.object({ status: z.enum(['queued', 'running', 'succeeded', 'awaiting_evidence', 'interrupted', 'failed', 'cancelled']), attempt: z.number().int().min(0).max(5), node: z.enum(['initialize', 'classifier', 'log_investigator', 'root_cause_analyst', 'resolution_generator', 'validator', 'collect_evidence', 'request_user_input', 'retry_analysis', 'validation_exhausted', 'final_report']).optional() }), timestamp: z.string().datetime({ offset: true }) })
export interface ProgressEvent { id: string; event: z.infer<typeof eventNames>; data: z.infer<typeof payloadSchema>['data']; timestamp: string }
export class StreamProtocolError extends Error { constructor() { super('The progress stream did not match the expected contract.') } }

/** Incremental WHATWG event framing; supports split UTF-8, CRLF and multiline data. */
export class SSEParser {
  private line = ''; private data: string[] = []; private id = ''; private event = ''; private skipLF = false; private bytes = 0
  constructor(private emit: (frame: { id: string; event: string; data: string }) => void) {}
  feed(text: string) {
    for (const char of text) {
      if (this.skipLF) { this.skipLF = false; if (char === '\n') continue }
      if (++this.bytes > 262144) throw new StreamProtocolError()
      if (char === '\r' || char === '\n') {
        this.consumeLine(this.line); this.line = ''; if (char === '\r') this.skipLF = true
      } else this.line += char
    }
  }
  private consumeLine(line: string) {
    if (!line) {
      if (this.data.length) this.emit({ id: this.id, event: this.event || 'message', data: this.data.join('\n') })
      this.data = []; this.id = ''; this.event = ''; this.bytes = 0; return
    }
    if (line.startsWith(':')) return
    const colon = line.indexOf(':'); const field = colon < 0 ? line : line.slice(0, colon)
    const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '')
    if (field === 'data') this.data.push(value)
    if (field === 'id' && !value.includes('\0')) this.id = value
    if (field === 'event') this.event = value
  }
}
export type StreamState = 'connecting' | 'live' | 'reconnecting' | 'complete' | 'error'
const pause = (ms: number, signal: AbortSignal) => new Promise<void>(resolve => {
  const done = () => { clearTimeout(timer); signal.removeEventListener('abort', done); resolve() }
  const timer = setTimeout(done, ms); signal.addEventListener('abort', done, { once: true }); if (signal.aborted) done()
})

/** Race both header acquisition and each body read against activity timeout.
 * A child controller cancels this connection only; navigation cancels the parent.
 */
class StreamIdleError extends Error { constructor() { super('Progress connection was idle; reconnecting from persisted progress.') } }
function activityDeadline<T>(operation: Promise<T>, controller: AbortController, milliseconds: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const cleanup = () => { clearTimeout(timer); controller.signal.removeEventListener('abort', aborted) }
    const aborted = () => { cleanup(); reject(new DOMException('Progress connection aborted', 'AbortError')) }
    const timer = setTimeout(() => { cleanup(); reject(new StreamIdleError()); controller.abort() }, milliseconds)
    controller.signal.addEventListener('abort', aborted, { once: true })
    if (controller.signal.aborted) aborted()
    operation.then(value => { cleanup(); resolve(value) }, error => { cleanup(); reject(error) })
  })
}
export function mergeProgress(previous: ProgressEvent[], event: ProgressEvent): ProgressEvent[] {
  if (previous.some(item => item.id === event.id)) return previous
  return [...previous, event].sort((a, b) => Number(a.id.split(':')[1]) - Number(b.id.split(':')[1]))
}

export async function streamProgress(api: Api, investigation: string, options: {
  signal: AbortSignal; onEvent: (event: ProgressEvent) => void; onState: (state: StreamState) => void; onError: (error: Error) => void; cursor?: string; sleep?: typeof pause; idleTimeoutMs?: number
}) {
  let cursor = options.cursor || ''; let attempts = 0; let ended = false
  const idle = options.idleTimeoutMs ?? 5000 // Backend heartbeat is one second.
  if (!Number.isFinite(idle) || idle <= 0) throw new Error('Invalid progress idle timeout')
  while (!options.signal.aborted && !ended) {
    options.onState(attempts ? 'reconnecting' : 'connecting')
    const connection = new AbortController()
    const abort = () => connection.abort()
    options.signal.addEventListener('abort', abort, { once: true })
    if (options.signal.aborted) { options.signal.removeEventListener('abort', abort); return }
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
    try {
      const response = await activityDeadline(api.response(`/investigations/${encodeURIComponent(investigation)}/events`, { signal: connection.signal, headers: cursor ? { 'Last-Event-ID': cursor } : {} }), connection, idle)
      if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) throw new StreamProtocolError()
      options.onState('live')
      reader = response.body.getReader(); const decoder = new TextDecoder()
      const parser = new SSEParser(frame => {
        if (ended || options.signal.aborted) return
        const match = /^([a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}):([1-9]\d*)$/i.exec(frame.id)
        if (!match || !Number.isSafeInteger(Number(match[2]))) throw new StreamProtocolError()
        if (cursor) {
          const [job, sequence] = cursor.split(':')
          if (match[1] !== job) throw new StreamProtocolError()
          if (Number(match[2]) <= Number(sequence)) return // Replayed/late events cannot roll state back.
          if (Number(match[2]) !== Number(sequence) + 1) throw new StreamProtocolError()
        } else if (Number(match[2]) !== 1) throw new StreamProtocolError()
        let parsed: z.infer<typeof payloadSchema>
        try { parsed = payloadSchema.parse(JSON.parse(frame.data)) } catch { throw new StreamProtocolError() }
        const event = eventNames.safeParse(frame.event); if (!event.success) throw new StreamProtocolError()
        const item: ProgressEvent = { id: frame.id, event: event.data, ...parsed }
        options.onEvent(item); cursor = frame.id; attempts = 0
        if (terminalEvents.has(item.event)) ended = true
      })
      while (!options.signal.aborted && !ended) {
        const { done, value } = await activityDeadline(reader.read(), connection, idle)
        if (done) { parser.feed(decoder.decode()); break }
        parser.feed(decoder.decode(value, { stream: true }))
      }
      if (options.signal.aborted) return
      if (ended) { options.onState('complete'); return }
      throw new ApiError(0)
    } catch (error) {
      if (options.signal.aborted) return
      if (error instanceof StreamProtocolError || (error instanceof ApiError && [400, 401, 403, 404, 422].includes(error.status)) || ++attempts > 5) {
        options.onState('error'); options.onError(error instanceof Error ? error : new ApiError(0)); return
      }
      options.onState('reconnecting')
    } finally {
      connection.abort()
      options.signal.removeEventListener('abort', abort)
      // Cancellation must not itself wait forever on an unresponsive transport.
      if (reader) { void reader.cancel().catch(() => {}); reader.releaseLock() }
    }
    await (options.sleep || pause)(Math.min(10000, 500 * 2 ** (attempts - 1)), options.signal)
  }
}
