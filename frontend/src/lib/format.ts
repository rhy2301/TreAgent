/** Pure presentation helpers. Nothing here recomputes snapshot values. */

/** Decimal string → grouped fixed-precision string. Parsing only for display. */
export function usdc(value: string | number | null | undefined, dp = 2): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)
  return n.toLocaleString('en-US', {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  })
}

/** Keeps sub-cent inference costs readable (0.0500 rather than 0.05). */
export function usdcFine(value: string | number | null | undefined): string {
  return usdc(value, 4)
}

export function num(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('en-US')
}

export function isPositive(value: string | number | null | undefined): boolean {
  if (value === null || value === undefined) return false
  const n = Number(value)
  return Number.isFinite(n) && n > 0
}

export function toNumber(value: string | number | null | undefined): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

/** Middle-elided address / signature: `3Hn8…vB1x`. */
export function shortHash(value: string | null | undefined, head = 4, tail = 4): string {
  if (!value) return '—'
  if (value.length <= head + tail + 1) return value
  return `${value.slice(0, head)}…${value.slice(-tail)}`
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.toLocaleDateString('en-CA')} ${d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
  })}`
}

/** "in 11h" style relative hint for due dates. */
export function untilDue(iso: string | null | undefined, from: string): string | null {
  if (!iso) return null
  const due = new Date(iso).getTime()
  const now = new Date(from).getTime()
  if (Number.isNaN(due) || Number.isNaN(now)) return null
  const hours = Math.round((due - now) / 3_600_000)
  if (hours < 0) return '기한 경과'
  if (hours < 24) return `${hours}시간 뒤`
  return `${Math.round(hours / 24)}일 뒤`
}

export async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value)
    return true
  } catch {
    return false
  }
}
