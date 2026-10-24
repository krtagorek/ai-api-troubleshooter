import { describe, it, expect, vi } from 'vitest'
import { SSEParser, streamProgress } from './sse'
import { Api, ApiError } from './api'
const job = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
const frame = (sequence: number, event = 'node_completed', node = 'classifier') => `id: ${job}:${sequence}\nevent: ${event}\ndata: ${JSON.stringify({ data: { status: event === 'succeeded' ? 'succeeded' : 'running', attempt: 1, node }, timestamp: '2026-10-22T10:00:00Z' })}\n\n`
function response(...chunks: string[]) { return new Response(new ReadableStream({ start(c) { chunks.forEach(s => c.enqueue(new TextEncoder().encode(s))); c.close() } }), { headers: { 'content-type': 'text/event-stream' } }) }
function callbacks() { return { signal: new AbortController().signal, onEvent: vi.fn(), onState: vi.fn(), onError: vi.fn(), sleep: vi.fn().mockResolvedValue(undefined) } }

describe('persisted SSE protocol', () => {
  it('frames CRLF split across chunks, comments and multiline data', () => {
    const emit = vi.fn(); const parser = new SSEParser(emit)
    for (const chunk of [': heartbeat\r', '\n\r\nid: 1\r\nevent: progress\r', '\ndata: first\r\ndata: second\r\n\r', '\n']) parser.feed(chunk)
    expect(emit).toHaveBeenCalledExactlyOnceWith({ id: '1', event: 'progress', data: 'first\nsecond' })
  })
  it('decodes a split UTF-8 stream and stops only at a durable terminal event', async () => {
    const api = new Api('secret'); vi.spyOn(api, 'response').mockResolvedValue(response(frame(1).slice(0, 34), frame(1).slice(34), frame(2, 'succeeded')))
    const cb = callbacks(); await streamProgress(api, 'inv', cb)
    expect(cb.onEvent).toHaveBeenCalledTimes(2); expect(cb.onState).toHaveBeenLastCalledWith('complete'); expect(cb.onError).not.toHaveBeenCalled()
  })
  it('reconnects with Last-Event-ID, ignores duplicates and preserves order', async () => {
    const api = new Api('secret'); const get = vi.spyOn(api, 'response').mockResolvedValueOnce(response(frame(1))).mockResolvedValueOnce(response(frame(1), frame(2, 'succeeded')))
    const cb = callbacks(); await streamProgress(api, 'inv', cb)
    expect(get.mock.calls[1][1]?.headers).toEqual({ 'Last-Event-ID': `${job}:1` })
    expect(cb.onEvent.mock.calls.map(c => c[0].id)).toEqual([`${job}:1`, `${job}:2`]); expect(cb.sleep).toHaveBeenCalledOnce()
  })
  it.each([401, 403, 404, 400])('does not retry authorization/cursor failure %i', async status => {
    const api = new Api('secret'); const get = vi.spyOn(api, 'response').mockRejectedValue(new ApiError(status)); const cb = callbacks()
    await streamProgress(api, 'inv', cb); expect(get).toHaveBeenCalledOnce(); expect(cb.onError).toHaveBeenCalledOnce()
  })
  it.each([frame(1, 'fabricated_answer'), frame(1, 'node_completed', 'execute_shell'), frame(1).replace('running', 'invented'), frame(1) + frame(3, 'succeeded')])('rejects invalid or noncontiguous events', async content => {
    const api = new Api('secret'); vi.spyOn(api, 'response').mockResolvedValue(response(content)); const cb = callbacks()
    await streamProgress(api, 'inv', cb); expect(cb.onState).toHaveBeenLastCalledWith('error'); expect(cb.onError).toHaveBeenCalledOnce(); expect(cb.sleep).not.toHaveBeenCalled()
  })
  it('bounds consecutive transient retries', async () => {
    const api = new Api('secret'); const get = vi.spyOn(api, 'response').mockRejectedValue(new ApiError(503)); const cb = callbacks()
    await streamProgress(api, 'inv', cb); expect(get).toHaveBeenCalledTimes(6); expect(cb.onError).toHaveBeenCalledOnce()
  })
  it('does not connect after unmount/abort', async () => {
    const api = new Api('secret'); const get = vi.spyOn(api, 'response'); const control = new AbortController(); control.abort(); await streamProgress(api, 'inv', { ...callbacks(), signal: control.signal }); expect(get).not.toHaveBeenCalled()
  })
  it('bounds incomplete frames', () => { expect(() => new SSEParser(vi.fn()).feed('x'.repeat(262145))).toThrow() })
})

describe('connection liveness', () => {
  it('aborts a half-open body and replays from its last durable ID', async () => {
    const api = new Api('secret'); let cancelled = false
    const stalled = new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(frame(1))) }, cancel() { cancelled = true } }), { headers: { 'content-type': 'text/event-stream' } })
    const get = vi.spyOn(api, 'response').mockResolvedValueOnce(stalled).mockResolvedValueOnce(response(frame(1), frame(2, 'succeeded')))
    const cb = callbacks(); await streamProgress(api, 'inv', { ...cb, idleTimeoutMs: 15 })
    expect(cancelled).toBe(true); expect(get.mock.calls[0][1]?.signal?.aborted).toBe(true)
    expect(get.mock.calls[1][1]?.headers).toEqual({ 'Last-Event-ID': `${job}:1` })
    expect(cb.onEvent).toHaveBeenCalledTimes(2); expect(cb.onState).toHaveBeenLastCalledWith('complete')
  })
  it('times out missing response headers, including transports that ignore abort', async () => {
    const api = new Api('secret'); const get = vi.spyOn(api, 'response').mockImplementationOnce(() => new Promise(() => {})).mockResolvedValueOnce(response(frame(1, 'succeeded')))
    const cb = callbacks(); await streamProgress(api, 'inv', { ...cb, idleTimeoutMs: 15 }); expect(get).toHaveBeenCalledTimes(2); expect(cb.onState).toHaveBeenLastCalledWith('complete')
  })
  it('resets the idle deadline on heartbeat comments but times out a missing heartbeat', async () => {
    const api = new Api('secret'); let pulses = 0; let timer: ReturnType<typeof setInterval>
    const heartbeat = new Response(new ReadableStream({ start(c) { timer = setInterval(() => { c.enqueue(new TextEncoder().encode(': heartbeat\n\n')); if (++pulses === 3) clearInterval(timer) }, 5) }, cancel() { clearInterval(timer) } }), { headers: { 'content-type': 'text/event-stream' } })
    const get = vi.spyOn(api, 'response').mockResolvedValueOnce(heartbeat).mockResolvedValueOnce(response(frame(1, 'succeeded')))
    await streamProgress(api, 'inv', { ...callbacks(), idleTimeoutMs: 25 }); expect(pulses).toBe(3); expect(get).toHaveBeenCalledTimes(2)
  })
  it('parent abort releases a stalled reader immediately without retries or post-navigation updates', async () => {
    const api = new Api('secret'); const control = new AbortController(); const cb = callbacks(); const get = vi.spyOn(api, 'response').mockResolvedValue(new Response(new ReadableStream({}), { headers: { 'content-type': 'text/event-stream' } }))
    const task = streamProgress(api, 'inv', { ...cb, signal: control.signal }); await new Promise(r => setTimeout(r, 5)); control.abort(); await task
    expect(get).toHaveBeenCalledOnce(); expect(cb.onError).not.toHaveBeenCalled(); expect(cb.sleep).not.toHaveBeenCalled()
  })
  it('ignores late and duplicate frames and never restarts after a terminal event', async () => {
    const api = new Api('secret'); const get = vi.spyOn(api, 'response').mockResolvedValue(response(frame(1), frame(2), frame(1), frame(2), frame(3, 'succeeded'), frame(4)))
    const cb = callbacks(); await streamProgress(api, 'inv', cb); expect(cb.onEvent.mock.calls.map(c => c[0].id)).toEqual([`${job}:1`,`${job}:2`,`${job}:3`]); expect(get).toHaveBeenCalledOnce(); expect(cb.sleep).not.toHaveBeenCalled()
  })
})

it('does not acquire a connection if navigation aborts in the state callback', async () => {
  const api = new Api('secret'); const get = vi.spyOn(api, 'response'); const controller = new AbortController()
  await streamProgress(api, 'inv', { ...callbacks(), signal: controller.signal, onState: () => controller.abort() })
  expect(get).not.toHaveBeenCalled()
})
