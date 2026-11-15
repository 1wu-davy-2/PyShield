import { CheckCircleFilled, CopyOutlined, WarningOutlined } from '@ant-design/icons'
import { App, Button, Tooltip } from 'antd'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { statusLabel, statusTone, type Tone } from '../lib/format'

/* -------------------------------------------------------------------------- */
/* brand                                                                      */
/* -------------------------------------------------------------------------- */

export function ShieldMark({ size = 30 }: { size?: number }) {
  return (
    <span className="brand-mark" style={{ width: size, height: size }}>
      <svg viewBox="0 0 32 32" aria-hidden="true">
        <path
          d="M16 6.5l7 2.6v6.2c0 4.4-2.8 8.1-7 9.6-4.2-1.5-7-5.2-7-9.6V9.1l7-2.6z"
          fill="none"
          stroke="#c6f24e"
          strokeWidth="1.9"
          strokeLinejoin="round"
        />
        <path
          d="M12.8 15.6l2.4 2.4 4.1-4.6"
          fill="none"
          stroke="#c6f24e"
          strokeWidth="1.9"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  )
}

export function Brand({ name }: { name: string }) {
  return (
    <Link to="/" className="brand" aria-label={name}>
      <ShieldMark />
      <span>
        {name}
        <span className="brand-sub">License Console</span>
      </span>
    </Link>
  )
}

/* -------------------------------------------------------------------------- */
/* status / edition                                                           */
/* -------------------------------------------------------------------------- */

export function StatusTag({
  status,
  label,
  tone,
}: {
  status?: string | null
  label?: string
  tone?: Tone
}) {
  return (
    <span className={`status-tag ${tone ?? statusTone(status)}`}>
      {label ?? statusLabel(status)}
    </span>
  )
}

export function EditionChip({ edition }: { edition?: string | null }) {
  const text =
    edition === 'trial' ? '免费版' : edition === 'pro' ? '专业版' : edition === 'enterprise' ? '企业版' : '—'
  return <span className="edition-chip">{text}</span>
}

/* -------------------------------------------------------------------------- */
/* copy helpers                                                               */
/* -------------------------------------------------------------------------- */

async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const area = document.createElement('textarea')
    area.value = text
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    return ok
  } catch {
    return false
  }
}

export function useCopy() {
  const { message } = App.useApp()
  return async (text: string, label = '已复制') => {
    const ok = await writeClipboard(text)
    if (ok) message.success(label)
    else message.warning('浏览器拒绝了复制，请手动选中文本')
  }
}

export function CopyButton({
  value,
  label = '复制',
  size = 'small',
  block = false,
}: {
  value: string
  label?: string
  size?: 'small' | 'middle'
  block?: boolean
}) {
  const copy = useCopy()
  return (
    <Tooltip title={value.length > 48 ? value : undefined}>
      <Button
        size={size}
        icon={<CopyOutlined />}
        block={block}
        onClick={() => copy(value)}
        disabled={!value}
      >
        {label}
      </Button>
    </Tooltip>
  )
}

export function CodeBlock({ value, label = '复制' }: { value: string; label?: string }) {
  return (
    <div className="copy-row">
      <code className="mono" style={{ wordBreak: 'break-all' }}>
        {value || '—'}
      </code>
      <CopyButton value={value} label={label} />
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* layout helpers                                                             */
/* -------------------------------------------------------------------------- */

export function SectionCard({
  title,
  extra,
  children,
  flush = false,
  className = '',
}: {
  title: ReactNode
  extra?: ReactNode
  children: ReactNode
  flush?: boolean
  className?: string
}) {
  return (
    <section className={`section-card ${className}`.trim()}>
      <div className="section-card-head">
        <h2>{title}</h2>
        {extra}
      </div>
      <div className={flush ? 'section-card-body flush' : 'section-card-body'}>{children}</div>
    </section>
  )
}

export function MetaList({ items }: { items: Array<[ReactNode, ReactNode]> }) {
  return (
    <dl className="meta-list">
      {items.map(([term, value], index) => (
        <div key={index}>
          <dt>{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  )
}

export function DetailItem({
  label,
  value,
  mono = false,
}: {
  label: string
  value: ReactNode
  mono?: boolean
}) {
  return (
    <div className="detail-item">
      <div className="detail-label">{label}</div>
      <div className={mono ? 'detail-value mono' : 'detail-value'}>{value ?? '—'}</div>
    </div>
  )
}

export function Callout({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'ok' | 'warn' | 'danger'
  children: ReactNode
}) {
  return <div className={`callout ${tone}`.trim()}>{children}</div>
}

export function LoadingScreen({ text = '正在加载…' }: { text?: string }) {
  return <div className="loading-screen">{text}</div>
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <Callout tone="danger">
      <WarningOutlined style={{ marginTop: 3 }} />
      <span>{children}</span>
    </Callout>
  )
}

export function SuccessNote({ children }: { children: ReactNode }) {
  return (
    <Callout tone="ok">
      <CheckCircleFilled style={{ marginTop: 3 }} />
      <span>{children}</span>
    </Callout>
  )
}

export function EmptyRow({ text }: { text: string }) {
  return <div className="empty-state">{text}</div>
}
