import { ScanLine, FileSearch, GitBranch, Lightbulb, ShieldCheck, Check, Circle } from 'lucide-react'
import type { ProgressEvent } from '@/lib/sse'
import { reconstructTimeline } from '@/lib/timeline'
export const agentSteps = [
  { id: 'classifier', name: 'Error classifier', detail: 'Categorizes the observed failure', icon: ScanLine },
  { id: 'log_investigator', name: 'Log investigator', detail: 'Extracts grounded evidence', icon: FileSearch },
  { id: 'root_cause_analyst', name: 'Root cause analyst', detail: 'Compares supported hypotheses', icon: GitBranch },
  { id: 'resolution_generator', name: 'Resolution generator', detail: 'Proposes steps for human review', icon: Lightbulb },
  { id: 'validator', name: 'Validator', detail: 'Reports only configured checks', icon: ShieldCheck },
]
export function Timeline({ events, compact = false }: { events: ProgressEvent[]; compact?: boolean }) {
  const { completed, attempts, incomplete } = reconstructTimeline(events)
  return <div>
    <p className="px-5 pt-4 text-[11px] text-muted-foreground">{attempts.length ? `Worker attempts: ${attempts.join(', ')}. Prior wrapper completions are retained across recovery.` : 'No persisted worker attempt recorded.'}</p>
    <p className="px-5 pt-2 text-[11px] text-muted-foreground">Wrapper completion does not prove model invocation, authorization, or validation. Denied, skipped, or interrupted agent execution is not distinguishable in these progress events.</p>
    {incomplete && <p role="status" className="px-5 pt-2 text-xs text-amber-700">Progress history is incomplete or conflicting. Missing execution states are unknown.</p>}
    <ol aria-label="Agent timeline" className={`timeline ${compact ? 'p-5' : 'p-6'}`}>{agentSteps.map(step => {
      const receipts = completed.get(step.id)
      return <li key={step.id} data-node={step.id} data-wrapper-completed={!!receipts} className="relative flex gap-3 pb-6 last:pb-0">
        <span className={`relative z-10 flex size-8 shrink-0 items-center justify-center rounded-lg border ${receipts ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-slate-200 bg-white text-slate-400'}`}><step.icon size={15} /></span>
        <div className="min-w-0 flex-1 pt-0.5"><div className="flex items-center justify-between gap-2"><h3 className="text-xs font-semibold">{step.name}</h3>{receipts ? <Check size={13} className="text-emerald-600" aria-label="Workflow wrapper completed" /> : <Circle size={7} className="text-slate-300" />}</div>
          <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{receipts ? `Workflow wrapper completed · attempt${receipts.length > 1 ? 's' : ''} ${receipts.join(', ')}` : 'No wrapper completion recorded'}</p>
          <p className="mt-1 text-[11px] leading-4 text-muted-foreground">Model invocation: not recorded</p>
        </div>
      </li>
    })}</ol>
  </div>
}
