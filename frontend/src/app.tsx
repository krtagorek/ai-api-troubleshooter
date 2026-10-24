import { Component, useState, type ReactNode } from 'react'
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SessionProvider, useSession } from '@/lib/session'
import { ApiError } from '@/lib/api'
import { Layout } from '@/components/layout'
import { Dashboard } from '@/pages/dashboard'
import { Troubleshooter } from '@/pages/troubleshooter'
import { IncidentHistory, IncidentPage } from '@/pages/incidents'
import { DiagnosticContracts } from '@/pages/diagnostic-contracts'
import { RegistryPage } from '@/pages/registry'

class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() { return this.state.failed ? <main className="mx-auto max-w-lg p-10"><h1 className="text-xl font-semibold">The workspace could not be displayed.</h1><p className="my-4 text-sm">Reload to reconnect. Your in-memory session will be cleared.</p><button className="underline" onClick={() => window.location.reload()}>Reload workspace</button></main> : this.props.children }
}
export function createQueryClient() { return new QueryClient({ defaultOptions: { queries: { staleTime: 15000, refetchOnWindowFocus: false, retry: (count, error) => !(error instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(error.status)) && count < 2 }, mutations: { retry: false } } }) }
export function App() { return <ErrorBoundary><BrowserRouter><Routes><Route element={<Layout />}><Route index element={<Dashboard />} /><Route path="troubleshooter" element={<Troubleshooter />} /><Route path="incidents" element={<IncidentHistory />} /><Route path="incidents/:id" element={<IncidentPage />} /><Route path="diagnostic-contracts" element={<DiagnosticContracts />} /><Route path="registry" element={<RegistryPage />} /><Route path="approvals" element={<RegistryPage admin />} /><Route path="*" element={<div className="py-20 text-center"><h1 className="text-2xl font-semibold">Page not found</h1><Link className="mt-4 inline-block text-primary underline" to="/">Return to dashboard</Link></div>} /></Route></Routes></BrowserRouter></ErrorBoundary> }
function SessionApp() { const { generation } = useSession(); return <App key={generation} /> }
export function Root() { const [client] = useState(createQueryClient); return <QueryClientProvider client={client}><SessionProvider><SessionApp /></SessionProvider></QueryClientProvider> }
