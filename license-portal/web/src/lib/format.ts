import dayjs from 'dayjs'

export const EDITION_LABEL: Record<string, string> = {
  trial: '免费版',
  pro: '专业版',
  enterprise: '企业版',
}

export const STATUS_LABEL: Record<string, string> = {
  active: '生效中',
  expired: '已过期',
  revoked: '已吊销',
  unused: '未使用',
  pending: '待处理',
  paid: '已收款',
  fulfilled: '已完成',
  cancelled: '已取消',
  granted: '已签发',
  unknown: '未找到',
  pending_email: '待验证邮箱',
  verified: '已完成',
}

export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral'

export const STATUS_TONE: Record<string, Tone> = {
  active: 'ok',
  granted: 'ok',
  fulfilled: 'ok',
  paid: 'ok',
  verified: 'ok',
  expired: 'warn',
  unused: 'info',
  pending: 'info',
  pending_email: 'info',
  revoked: 'danger',
  cancelled: 'neutral',
  unknown: 'neutral',
}

export function editionLabel(edition?: string | null): string {
  const key = String(edition ?? '').toLowerCase()
  return EDITION_LABEL[key] ?? (edition || '—')
}

export function statusLabel(status?: string | null): string {
  const key = String(status ?? '').toLowerCase()
  return STATUS_LABEL[key] ?? (status || '—')
}

export function statusTone(status?: string | null): Tone {
  return STATUS_TONE[String(status ?? '').toLowerCase()] ?? 'neutral'
}

export function formatDate(value?: string | null): string {
  if (!value) return '—'
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD') : String(value)
}

export function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm') : String(value)
}

export function daysLeft(expires?: string | null): number | null {
  if (!expires) return null
  const parsed = dayjs(expires)
  if (!parsed.isValid()) return null
  return parsed.endOf('day').diff(dayjs(), 'day')
}

export function expiryHint(expires?: string | null): string {
  const left = daysLeft(expires)
  if (left === null) return '长期有效'
  if (left < 0) return `已过期 ${Math.abs(left)} 天`
  if (left === 0) return '今天到期'
  if (left <= 30) return `剩余 ${left} 天`
  return ''
}

export function remainingShort(expires?: string | null): string {
  const left = daysLeft(expires)
  if (left === null) return '长期有效'
  if (left < 0) return `已过期 ${Math.abs(left)} 天`
  if (left === 0) return '今天到期'
  return `${left} 天`
}

export function remainingText(expires?: string | null): string {
  const left = daysLeft(expires)
  if (left === null) return '长期有效'
  if (left < 0) return `已过期 ${Math.abs(left)} 天`
  if (left === 0) return '今天到期'
  return `剩余 ${left} 天`
}

export function shortMachine(machine?: string | null, size = 14): string {
  const text = String(machine ?? '')
  if (!text) return '—'
  return text.length > size * 2 ? `${text.slice(0, size)}…${text.slice(-4)}` : text
}

export function shortSerial(serial?: string | null): string {
  return String(serial ?? '') || '—'
}

export function formatBytesLike(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

export function formatDetail(detail: unknown): string {
  if (detail === null || detail === undefined) return ''
  if (typeof detail === 'string') return detail
  if (typeof detail === 'object') {
    const entries = Object.entries(detail as Record<string, unknown>)
    if (!entries.length) return ''
    return entries
      .map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : value}`)
      .join(' · ')
  }
  return String(detail)
}

export function maskKey(prefix?: string | null): string {
  const text = String(prefix ?? '')
  return text ? `${text}-••••-••••-••••` : '—'
}

export function percent(value: number, total: number): number {
  if (!total) return 0
  return Math.round((value / total) * 100)
}
