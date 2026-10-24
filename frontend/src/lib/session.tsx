import { createContext, useContext, useState, useMemo, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Api } from './api'
interface Session { generation: number; api: Api | null; connect: (token: string) => Promise<void>; disconnect: () => void }
const Context = createContext<Session | null>(null)
export function SessionProvider({ children }: { children: ReactNode }) {
  const [api, setApi] = useState<Api | null>(null); const [generation, setGeneration] = useState(0); const cache = useQueryClient()
  const value = useMemo(() => ({ api, generation, connect: async (token: string) => {
    const candidate = new Api(token.trim()); await candidate.incidents()
    await cache.cancelQueries(); cache.clear(); setGeneration(n => n + 1); setApi(candidate)
  }, disconnect: () => { setGeneration(n => n + 1); setApi(null); void cache.cancelQueries(); cache.clear() } }), [api, cache, generation])
  return <Context.Provider value={value}>{children}</Context.Provider>
}
export function useSession() { const value = useContext(Context); if (!value) throw new Error('Session provider required'); return value }
