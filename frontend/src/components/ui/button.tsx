// shadcn/ui source-owned Button, customized for this workspace.
import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'
const buttonVariants = cva('inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4', {
  variants: { variant: { default: 'bg-primary text-primary-foreground hover:bg-primary/90', outline: 'border border-border bg-white hover:bg-muted', ghost: 'hover:bg-muted', destructive: 'bg-destructive text-white hover:bg-destructive/90' }, size: { default: 'h-10 px-4 py-2', sm: 'h-8 px-3 text-xs', icon: 'h-10 w-10' } }, defaultVariants: { variant: 'default', size: 'default' },
})
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> { asChild?: boolean }
export function Button({ className, variant, size, asChild, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : 'button'
  return <Comp className={cn(buttonVariants({ variant, size, className }))} {...props} />
}
