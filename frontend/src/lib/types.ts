import type { components } from './openapi'
export type Incident = components['schemas']['Incident']
export type IncidentSubmission = components['schemas']['IncidentSubmission']
export type IncidentDetail = components['schemas']['IncidentDetail']
export type IncidentPage = components['schemas']['IncidentPage']
type BackendReport = components['schemas']['FinalReport']
export type Report = Omit<BackendReport, 'diagnosis' | 'proposed_fix'> & {
  diagnosis: BackendReport['diagnosis'] & { diagnostic_checks?: string[] }
  proposed_fix?: (NonNullable<BackendReport['proposed_fix']> & { plan_kind?: 'diagnostic' | 'remediation' }) | null
}
export type Registration = components['schemas']['ApiRegistration']
export type Policy = components['schemas']['ApiPolicy']
export type Challenge = components['schemas']['RegistrationResponse']
export interface AuditEvent { action: string; outcome: string; reason: string; revision: number | null; actor_id: string; event_id: string; timestamp: string }
export interface Receipt { job_id: string; status: 'accepted' }
