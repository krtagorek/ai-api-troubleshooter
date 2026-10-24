import type { ComponentProps } from 'react'
import { cn } from '@/lib/utils'
export function Input({ className, ...props }: ComponentProps<'input'>) { return <input className={cn('flex h-10 w-full rounded-lg border border-input bg-white px-3 text-sm outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/20 disabled:opacity-50', className)} {...props} /> }
export function Textarea({ className, ...props }: ComponentProps<'textarea'>) { return <textarea className={cn('min-h-24 w-full rounded-lg border border-input bg-white p-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-primary/20', className)} {...props} /> }
