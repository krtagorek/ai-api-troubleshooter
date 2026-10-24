import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
export function cn(...inputs: ClassValue[]) { return twMerge(clsx(inputs)) }
export function humanize(value: string) { return value.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase()) }
