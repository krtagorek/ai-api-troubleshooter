import { useState } from 'react'
import { NavLink, Outlet, useLocation, Link } from 'react-router-dom'
import { Activity, LayoutDashboard, MessagesSquare, History, Network, ShieldCheck, ArrowUpRight, LogOut, Menu, X, Workflow, CircleHelp } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useSession } from '@/lib/session'
import { baseUrl } from '@/lib/api'
import { Button } from './ui/button'
import { ConnectionButton } from './common'
const navigation = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/troubleshooter', label: 'AI Troubleshooter', icon: MessagesSquare },
  { to: '/incidents', label: 'Incident history', icon: History },
  { to: '/registry', label: 'API Registry', icon: Network },
  { to: '/diagnostic-contracts', label: 'Diagnostic contracts', icon: ShieldCheck },
  { to: '/approvals', label: 'Admin approvals', icon: ShieldCheck },
]
export function Layout() {
  const [open, setOpen] = useState(false); const { api, disconnect } = useSession(); const location = useLocation()
  const health = useQuery({ queryKey: ['health'], queryFn: async ({ signal }) => { const r = await fetch(`${baseUrl}/health/ready`, { signal, redirect: 'error', credentials: 'omit' }); if (!r.ok) throw new Error(); return r.json() }, refetchInterval: 30000, retry: false })
  const section = navigation.find(n => n.to === '/' ? location.pathname === '/' : location.pathname.startsWith(n.to))?.label || 'Incident details'
  return <div className="min-h-dvh bg-background"><a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-5 focus:top-5 focus:z-50 focus:bg-white focus:p-3">Skip to content</a>
    {open && <button className="fixed inset-0 z-20 bg-slate-950/40 lg:hidden" aria-label="Close navigation overlay" onClick={() => setOpen(false)} />}
    <aside className={`sidebar fixed inset-y-0 left-0 z-30 flex w-[244px] flex-col p-5 transition-transform lg:translate-x-0 ${open ? 'translate-x-0' : '-translate-x-full'}`}>
      <Link to="/" className="mb-9 mt-2 flex items-center gap-3 px-1" onClick={() => setOpen(false)}><span className="brand-icon"><Workflow size={24} /></span><span className="text-[15px] font-semibold tracking-tight text-white">API Troubleshooter<span className="mt-0.5 block text-[10px] font-medium uppercase tracking-[0.22em] text-teal-100/45">Integration workspace</span></span></Link>
      <div className="mb-6 rounded-lg border border-white/10 bg-white/5 px-3 py-3"><p className="text-[10px] font-semibold uppercase tracking-widest text-white/40">Workspace</p><p className="mt-1 flex items-center justify-between text-sm text-white/85">Diagnostic operations<span className="rounded border border-white/15 px-1.5 text-[10px] text-white/50">API</span></p></div>
      <p className="mb-3 px-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/35">Platform</p><nav aria-label="Main navigation" className="space-y-1.5">{navigation.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} onClick={() => setOpen(false)} className={({ isActive }) => `flex items-center gap-3 rounded-lg px-3 py-3 text-[13px] font-medium transition-colors ${isActive ? 'bg-[#26524c] text-[#b8edcf] shadow-[inset_3px_0_0_#a4dfbe]' : 'text-white/60 hover:bg-white/5 hover:text-white'}`}><Icon size={17} />{label}</NavLink>)}</nav>
      <div className="mt-auto"><div className="mb-5 rounded-xl border border-white/10 bg-white/[0.035] p-4"><ShieldCheck className="mb-3 text-[#a4dfbe]" size={22} /><p className="text-sm font-medium text-white/90">Evidence before action.</p><p className="mt-2 text-xs leading-5 text-white/45">Approved endpoints. Grounded diagnosis. Human-reviewed fixes.</p></div><p className="flex items-center gap-2 px-2 text-xs text-white/40"><CircleHelp size={14} />Built for API investigation</p></div>
    </aside>
    <div className="lg:pl-[244px]"><header className="flex min-h-[76px] items-center justify-between gap-3 border-b border-border bg-white/90 px-5 md:px-9"><div className="flex items-center gap-3"><Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setOpen(!open)} aria-label="Toggle navigation">{open ? <X /> : <Menu />}</Button><p className="text-xs text-muted-foreground"><span className="hidden sm:inline">Workspace <span className="mx-3 text-slate-300">/</span></span><span className="font-medium text-slate-700">{section}</span></p></div><div className="flex items-center gap-3"><span className="hidden items-center gap-1.5 text-[11px] text-muted-foreground md:inline-flex"><span className={`size-1.5 rounded-full ${health.isSuccess ? 'bg-emerald-500' : health.isPending ? 'bg-slate-400' : 'bg-amber-500'}`} />{health.isSuccess ? 'API ready' : health.isPending ? 'Checking API' : 'API unavailable'}</span>{api ? <Button variant="outline" size="sm" onClick={disconnect}><LogOut />Disconnect</Button> : <ConnectionButton variant="outline" />}</div></header>
      <main id="main" className="mx-auto max-w-[1536px] p-5 md:p-9"><Outlet /></main><footer className="mx-5 mb-6 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-5 text-[11px] text-muted-foreground md:mx-9"><span className="flex items-center gap-2"><Activity size={13} />AI API Integration Troubleshooter</span><span>Powered by LangGraph <ArrowUpRight size={12} className="ml-1 inline" /></span></footer>
    </div></div>
}
