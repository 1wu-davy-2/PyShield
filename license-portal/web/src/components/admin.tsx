import type { ReactNode } from 'react'

export function AdminHeader({
  title,
  sub,
  children,
}: {
  title: ReactNode
  sub?: ReactNode
  children?: ReactNode
}) {
  return (
    <div className="admin-topbar">
      <div>
        <h1 className="admin-title">{title}</h1>
        {sub ? <p className="admin-sub">{sub}</p> : null}
      </div>
      {children ? <div className="admin-actions">{children}</div> : null}
    </div>
  )
}

export function Kpi({
  label,
  value,
  hint,
  accent = false,
  marker,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  accent?: boolean
  marker?: ReactNode
}) {
  return (
    <div className={accent ? 'kpi accent' : 'kpi'}>
      <div className="kpi-label">
        <span>{label}</span>
        {marker}
      </div>
      <div className="kpi-value">{value}</div>
      {hint ? <div className="kpi-hint">{hint}</div> : null}
    </div>
  )
}

export const TABLE_PAGINATION = {
  pageSize: 20,
  showSizeChanger: false,
  hideOnSinglePage: true,
  showTotal: (total: number) => `共 ${total} 条`,
}
