import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'

export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ')
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'bg-card text-card-foreground border border-border rounded-lg',
        'shadow-[0_1px_2px_0_rgb(0_0_0_/_0.18)]',
        className,
      )}
      {...props}
    />
  )
}

export function CardHeader({
  title,
  subtitle,
  action,
}: {
  title: ReactNode
  subtitle?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-border">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
        {subtitle && <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>}
      </div>
      {action}
    </div>
  )
}

type BadgeTone = 'default' | 'muted' | 'destructive' | 'entry' | 'outline'

const badgeTones: Record<BadgeTone, string> = {
  default: 'bg-primary text-primary-foreground border-transparent',
  muted: 'bg-muted text-muted-foreground border-transparent',
  destructive: 'bg-destructive text-destructive-foreground border-transparent',
  // chart-2 (blue) rather than a green: the palette has no green, and
  // pairing blue/red instead of green/red stays legible for the ~8% of
  // men with red-green colour vision deficiency.
  entry: 'bg-[var(--chart-2)] text-white border-transparent',
  outline: 'bg-transparent text-foreground border-border',
}

export function Badge({
  tone = 'default',
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5',
        'text-[11px] font-medium leading-none whitespace-nowrap',
        badgeTones[tone],
        className,
      )}
      {...props}
    />
  )
}

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'destructiveGhost'

const buttonVariants: Record<ButtonVariant, string> = {
  primary: 'bg-primary text-primary-foreground hover:opacity-90',
  secondary: 'bg-secondary text-secondary-foreground hover:bg-accent',
  ghost: 'bg-transparent text-muted-foreground hover:bg-accent hover:text-accent-foreground',
  destructiveGhost: 'bg-transparent text-muted-foreground hover:bg-destructive hover:text-destructive-foreground',
}

export function Button({
  variant = 'primary',
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-md',
        'px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
        'focus-visible:ring-offset-background disabled:opacity-50 disabled:cursor-not-allowed',
        buttonVariants[variant],
        className,
      )}
      {...props}
    />
  )
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'h-8 rounded-md border border-input bg-background px-2.5 text-xs',
        'placeholder:text-muted-foreground',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
        'focus-visible:ring-offset-background',
        className,
      )}
      {...props}
    />
  )
}

export function EmptyState({ icon, title, hint }: { icon?: ReactNode; title: string; hint?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon && <div className="text-muted-foreground opacity-60">{icon}</div>}
      <p className="text-sm font-medium">{title}</p>
      {hint && <p className="text-xs text-muted-foreground max-w-sm leading-relaxed">{hint}</p>}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-md bg-muted', className)} />
}
