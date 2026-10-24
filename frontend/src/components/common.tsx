import { useState, type ReactNode, type FormEvent } from 'react'
import { KeyRound, LockKeyhole, ShieldCheck, Unplug, ArrowUpRight, LoaderCircle, CircleAlert } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Button } from './ui/button'
import { Badge } from './ui/badge'
import { Dialog, DialogContent, DialogTitle, DialogDescription, DialogTrigger } from './ui/dialog'
import { Input } from './ui/input'
import { ApiError } from '@/lib/api'
import { useSession } from '@/lib/session'
import { humanize } from '@/lib/utils'

export function Panel({ children, className = '' }: { children: ReactNode; className?: string }) { return <section className={`rounded-xl border border-border bg-white ${className}`}>{children}</section> }
export function PageHeading({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) { return <div className="mb-7 flex flex-wrap items-end justify-between gap-5"><div><p className="eyebrow">{eyebrow}</p><h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-900 md:text-[34px]">{title}</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">{description}</p></div>{action}</div> }
export function ErrorNotice({ error }: { error: unknown }) { if (!error) return null; return <div role="alert" className="my-3 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"><CircleAlert size={17} className="mt-0.5 shrink-0" /><div>{error instanceof ApiError ? error.message : 'The action could not be completed. Check your inputs and try again.'}{error instanceof ApiError && error.correlation && <p className="mt-1 break-all font-mono text-xs">Reference: {error.correlation}</p>}</div></div> }
export function Loading() { return <p role="status" className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><LoaderCircle size={16} className="animate-spin" />Loading workspace data…</p> }
export function StatusBadge({ status }: { status: string }) {
  const green = ['approved', 'completed', 'succeeded', 'passed', 'supported', 'connected'].includes(status)
  const red = ['failed', 'revoked', 'critical', 'high'].includes(status)
  return <Badge className={green ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : red ? 'border-red-200 bg-red-50 text-red-700' : 'border-slate-200 bg-slate-50 text-slate-600'}><span className={`size-1.5 rounded-full ${green ? 'bg-emerald-500' : red ? 'bg-red-400' : 'bg-slate-400'}`} />{humanize(status)}</Badge>
}
export function ConnectionButton({ label = 'Connect API', variant = 'default' }: { label?: string; variant?: 'default' | 'outline' }) {
  const session = useSession(); const [open, setOpen] = useState(false); const [token, setToken] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState<unknown>()
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); setError(undefined); try { await session.connect(token); setOpen(false) } catch (e) { setError(e) } finally { setBusy(false); setToken('') } }
  return <Dialog open={open} onOpenChange={value => { setOpen(value); if (!value) { setToken(''); setError(undefined) } }}><DialogTrigger asChild><Button variant={variant}><KeyRound />{label}</Button></DialogTrigger><DialogContent><DialogTitle className="text-xl font-semibold">Connect your workspace</DialogTitle><DialogDescription className="mt-2 text-sm leading-6 text-muted-foreground">Use a bearer token accepted by your backend. Tokens stay in memory and are cleared when you disconnect or reload.</DialogDescription><form onSubmit={submit} className="mt-6 space-y-4"><label className="field-label" htmlFor="session-token">Bearer token</label><Input id="session-token" type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} required maxLength={8192} /><ErrorNotice error={error} /><Button type="submit" disabled={busy || !token.trim()} className="w-full">{busy ? <LoaderCircle className="animate-spin" /> : <LockKeyhole />}Connect securely</Button><p className="text-xs leading-5 text-muted-foreground">Demo authentication must be explicitly enabled on a development backend. Permissions are checked by the server.</p></form></DialogContent></Dialog>
}
export function SessionRequired({ children }: { children: ReactNode }) {
  const { api } = useSession()
  return api ? children : <Panel className="flex flex-col items-center px-6 py-14 text-center"><div className="mb-4 rounded-2xl bg-emerald-50 p-4 text-primary"><Unplug size={27} /></div><h2 className="text-lg font-semibold">Your workspace, connected</h2><p className="mb-5 mt-2 max-w-md text-sm leading-6 text-muted-foreground">Connect to load your authorized incidents and API policies. No sample records or AI responses are shown.</p><ConnectionButton /></Panel>
}
export function Empty({ title, description, action }: { title: string; description: string; action?: ReactNode }) { return <div className="flex min-h-48 flex-col items-center justify-center gap-2 px-5 py-9 text-center"><div className="mb-2 rounded-xl bg-muted p-3 text-primary"><ShieldCheck size={23} /></div><h3 className="font-semibold">{title}</h3><p className="max-w-md text-sm leading-6 text-muted-foreground">{description}</p>{action && <div className="mt-3">{action}</div>}</div> }
export function TextLink({ to, children }: { to: string; children: ReactNode }) { return <Link to={to} className="inline-flex items-center gap-1.5 text-xs font-semibold text-primary hover:underline">{children}<ArrowUpRight size={14} /></Link> }
