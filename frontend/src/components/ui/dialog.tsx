// shadcn/ui source-owned Dialog using accessible Radix primitives.
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ComponentProps } from 'react'
export const Dialog = DialogPrimitive.Root
export const DialogTrigger = DialogPrimitive.Trigger
export const DialogTitle = DialogPrimitive.Title
export const DialogDescription = DialogPrimitive.Description
export function DialogContent({ children, className, ...props }: ComponentProps<typeof DialogPrimitive.Content>) {
  return <DialogPrimitive.Portal><DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-slate-950/45 backdrop-blur-sm" /><DialogPrimitive.Content className={cn('fixed left-1/2 top-1/2 z-50 max-h-[90dvh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border bg-white p-6 shadow-xl', className)} {...props}>{children}<DialogPrimitive.Close className="absolute right-4 top-4 rounded p-1 text-muted-foreground focus-visible:ring-2" aria-label="Close dialog"><X size={18} /></DialogPrimitive.Close></DialogPrimitive.Content></DialogPrimitive.Portal>
}
