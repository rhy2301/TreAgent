import type { GuardRow } from '../types'
import { CHECK_GLYPH, checkTone, type CheckTone } from '../lib/derive'

const TONE: Record<CheckTone, { text: string; row: string; label: string }> = {
  PASS: { text: 'text-mint', row: '', label: 'PASS' },
  CLAMP: {
    text: 'text-warning',
    row: 'border-warning/40 bg-warning/8',
    label: 'CLAMPED',
  },
  FAIL: { text: 'text-danger', row: 'border-danger/45 bg-danger/8', label: 'FAIL' },
  SKIP: { text: 'text-muted', row: '', label: 'SKIP' },
}

/**
 * guard.checks[] verbatim. During VALIDATING the rows appear one by one, which
 * is what makes the policy layer legible as a *sequence* of independent checks.
 */
export function PolicyChecklist({
  guard,
  visibleCount,
}: {
  guard: GuardRow
  /** How many rows to reveal; pass Infinity when not animating. */
  visibleCount: number
}) {
  return (
    <div className="rounded-[12px] border border-border bg-elevated/40 p-1">
      {guard.checks.map((check, i) => {
        if (i >= visibleCount) return null
        const tone = TONE[checkTone(check)]
        const glyph = CHECK_GLYPH[checkTone(check)]
        return (
          <div
            key={`${check.id}-${i}`}
            style={{ animationDelay: `${Math.min(i, 10) * 20}ms` }}
            className={`flex animate-[rise_220ms_ease-out_both] items-center gap-3 rounded-[9px] border border-transparent px-2.5 py-[7px] ${tone.row}`}
          >
            <span className={`w-3 shrink-0 text-center text-[12px] ${tone.text}`}>
              {glyph}
            </span>
            <span className="w-[168px] shrink-0 truncate text-[11.5px] text-txt-2">
              {check.label}
            </span>
            <span className="mono min-w-0 flex-1 truncate text-[10.5px] text-muted">
              {check.note ?? ''}
            </span>
            <span
              className={`mono w-[62px] shrink-0 text-right text-[10px] tracking-wide ${tone.text}`}
            >
              {tone.label}
            </span>
          </div>
        )
      })}
    </div>
  )
}
