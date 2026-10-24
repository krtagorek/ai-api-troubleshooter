import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ReportView } from './report'
import type { Report } from '@/lib/types'

describe('tentative unvalidated diagnostic report', () => {
  it('separates completed analysis from validation and keeps human approval visible', () => {
    const report: Report = {
      incident_id: 'synthetic-report', summary: 'Tentative diagnostic analysis completed; validation NOT_TESTED and human approval required',
      diagnosis: { status: 'tentative', summary: 'Qualified mechanism; origin unconfirmed', confidence: 0.5,
        selected_hypothesis_id: 'h', uncertainty: ['Origin remains unconfirmed'], missing_evidence: [],
        diagnostic_checks: ['Inspect sanitized coordinator metadata'] },
      proposed_fix: { plan_kind: 'diagnostic', summary: 'Review observed routing', steps: ['Inspect sanitized epochs'],
        prerequisites: [], risks: [], verification_steps: ['Obtain approved validation'], requires_human_approval: true, evidence_ids: ['ev:fixture'] },
      evidence: { completeness: 'partial', missing: [], items: [{ evidence_id: 'ev:fixture', source: 'log', observation: '41',
        reference: '/logs/0/fields/routed_epoch', reliability: 0.5, ambiguous: false }] },
      validation_results: [{ status: 'NOT_TESTED', checks: [], issues: ['No tool executed'], retry_recommended: false }], limitations: ['No fix or checks executed'],
    }
    render(<ReportView report={report} />)
    expect(screen.getByLabelText('Persisted diagnostic report')).toBeVisible()
    expect(screen.getByText('Qualified mechanism; origin unconfirmed')).toBeVisible()
    expect(screen.getByText(/Confidence: 50%/)).toBeVisible()
    expect(screen.getByText('Inspect sanitized coordinator metadata')).toBeVisible()
    expect(screen.getByText('Inspect sanitized epochs')).toBeVisible()
    expect(screen.getByText(/Human approval is required/)).toBeVisible()
    expect(screen.getByText('NOT TESTED')).toBeVisible()
    expect(screen.queryByText('Passed', { exact: true })).not.toBeInTheDocument()
  })
})
