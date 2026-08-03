import { useState, type ReactNode } from 'react'
import { copyText } from '../lib/format'

type Tone = 'neutral' | 'mint' | 'ai' | 'warning' | 'danger' | 'info' | 'muted'

const TONE_BADGE: Record<Tone, string> = {
  neutral: 'border-border bg-elevated text-txt-2',
  mint: 'border-mint/35 bg-mint/10 text-mint',
  ai: 'border-ai/40 bg-ai/12 text-ai',
  warning: 'border-warning/35 bg-warning/10 text-warning',
  danger: 'border-danger/40 bg-danger/10 text-danger',
  info: 'border-info/35 bg-info/10 text-info',
  muted: 'border-border bg-bg-2 text-muted',
}

export const TONE_TEXT: Record<Tone, string> = {
  neutral: 'text-txt-2',
  mint: 'text-mint',
  ai: 'text-ai',
  warning: 'text-warning',
  danger: 'text-danger',
  info: 'text-info',
  muted: 'text-muted',
}

export function Badge({
  tone = 'neutral',
  mono,
  icon,
  children,
  className = '',
}: {
  tone?: Tone
  mono?: boolean
  icon?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-[3px] text-[11px] leading-none font-medium tracking-wide ${
        TONE_BADGE[tone]
      } ${mono ? 'mono' : ''} ${className}`}
    >
      {icon}
      {children}
    </span>
  )
}

export function Card({
  children,
  className = '',
  accent,
}: {
  children: ReactNode
  className?: string
  accent?: 'ai' | 'danger'
}) {
  const ring =
    accent === 'ai'
      ? 'border-ai/40 shadow-[0_0_0_1px_rgba(139,124,255,0.08),0_18px_40px_-28px_rgba(139,124,255,0.55)]'
      : accent === 'danger'
        ? 'border-danger/45'
        : 'border-border'
  return (
    <section
      className={`rounded-[16px] border bg-card shadow-card ${ring} ${className}`}
    >
      {children}
    </section>
  )
}

export function CardTitle({
  title,
  scope,
  right,
  sub,
  stack,
}: {
  title: string
  /** Network scope label — every card states which network it belongs to. */
  scope?: { label: string; tone: Tone }
  right?: ReactNode
  sub?: ReactNode
  /** Put the scope badge on its own line when the card is narrow. */
  stack?: boolean
}) {
  return (
    <header className="flex items-start justify-between gap-4 px-5 pt-4 pb-3">
      <div className="min-w-0">
        <div className={stack ? '' : 'flex items-center gap-2.5'}>
          <h2 className="text-[13px] font-semibold tracking-[0.02em] text-txt">{title}</h2>
          {scope && (
            <Badge
              tone={scope.tone}
              className={`whitespace-nowrap uppercase ${stack ? 'mt-1.5' : ''}`}
            >
              {scope.label}
            </Badge>
          )}
        </div>
        {sub && <div className="mt-1 text-[11px] text-muted">{sub}</div>}
      </div>
      {right}
    </header>
  )
}

export function Divider({ className = '' }: { className?: string }) {
  return <div className={`h-px w-full bg-border ${className}`} />
}

export function StatusDot({
  tone,
  pulse,
}: {
  tone: Tone
  pulse?: boolean
}) {
  const bg: Record<Tone, string> = {
    neutral: 'bg-txt-2',
    mint: 'bg-mint',
    ai: 'bg-ai',
    warning: 'bg-warning',
    danger: 'bg-danger',
    info: 'bg-info',
    muted: 'bg-muted',
  }
  return (
    <span
      className={`inline-block size-[7px] shrink-0 rounded-full ${bg[tone]} ${
        pulse ? 'animate-[pulse-soft_1.8s_ease-in-out_infinite]' : ''
      }`}
    />
  )
}

/** Copy-to-clipboard for sandbox proofs and unlinkable hashes. */
export function CopyButton({ value, label = 'Copy' }: { value: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      onClick={async () => {
        if (await copyText(value)) {
          setDone(true)
          window.setTimeout(() => setDone(false), 1400)
        }
      }}
      className="inline-flex items-center gap-1 rounded-md border border-border bg-elevated px-2 py-1 text-[11px] text-txt-2 transition-colors duration-150 hover:border-mint/40 hover:text-mint"
      title="클립보드에 복사"
    >
      {done ? '✓ Copied' : `${label} ⧉`}
    </button>
  )
}

/** Hover tooltip. Used to mark projected (non-on-chain) figures. */
export function Info({ text }: { text: string }) {
  return (
    <span className="group relative inline-flex align-middle">
      <span className="ml-1 inline-flex size-[14px] cursor-help items-center justify-center rounded-full border border-border text-[9px] leading-none text-muted transition-colors duration-150 group-hover:border-info/60 group-hover:text-info">
        i
      </span>
      <span className="pointer-events-none absolute bottom-[130%] right-0 z-30 w-[248px] rounded-[10px] border border-border bg-elevated px-3 py-2 text-[11px] leading-relaxed text-txt-2 opacity-0 shadow-card transition-opacity duration-150 group-hover:opacity-100">
        {text}
      </span>
    </span>
  )
}

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton ${className}`} />
}

export function ProgressBar({
  pct,
  tone = 'mint',
  className = '',
}: {
  pct: number
  tone?: Tone
  className?: string
}) {
  const fill: Record<Tone, string> = {
    neutral: 'bg-txt-2',
    mint: 'bg-mint',
    ai: 'bg-ai',
    warning: 'bg-warning',
    danger: 'bg-danger',
    info: 'bg-info',
    muted: 'bg-muted',
  }
  return (
    <div className={`h-[3px] w-full overflow-hidden rounded-full bg-elevated ${className}`}>
      <div
        className={`h-full rounded-full ${fill[tone]} transition-[width] duration-500 ease-out`}
        style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}
      />
    </div>
  )
}

export function Mono({
  children,
  className = '',
  title,
}: {
  children: ReactNode
  className?: string
  title?: string
}) {
  return (
    <span className={`mono ${className}`} title={title}>
      {children}
    </span>
  )
}
