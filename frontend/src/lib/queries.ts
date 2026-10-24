import { useQuery } from '@tanstack/react-query'
import { useSession } from './session'
export function useIncidents(offset = 0) { const { api } = useSession(); return useQuery({ queryKey: ['incidents', offset], queryFn: ({ signal }) => api!.incidents(offset, signal), enabled: !!api }) }
export function useRegistry() { const { api } = useSession(); return useQuery({ queryKey: ['registry'], queryFn: ({ signal }) => api!.registry(signal), enabled: !!api }) }
