import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import { App, Button, Input, Select, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'

import { api, queryString } from '../../api/client'
import { useAudit, useFilters } from '../../api/hooks'
import type { AuditEvent, ListResult } from '../../api/types'
import { AdminHeader, TABLE_PAGINATION } from '../../components/admin'
import { ErrorNote, SectionCard } from '../../components/ui'
import { formatDateTime, formatDetail } from '../../lib/format'

const ACTOR_LABEL: Record<string, string> = {
  admin: '管理员',
  client: '客户端',
  public: '访客',
  system: '系统',
}

function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? '' : String(value)
  return `"${text.replace(/"/g, '""')}"`
}

function auditCsv(items: AuditEvent[]): string {
  const header = [
    '时间', '动作', '操作者类型', '操作者', '序列号', '授权 ID', '激活码前缀', '详情',
  ]
  const rows = items.map((row) => [
    formatDateTime(row.at),
    row.action,
    ACTOR_LABEL[row.actor_type] ?? row.actor_type,
    row.actor,
    row.serial,
    row.license_id,
    row.key_prefix,
    formatDetail(row.detail),
  ])
  return '\ufeff' + [header, ...rows]
    .map((row) => row.map(csvCell).join(','))
    .join('\r\n')
}

export default function Audit() {
  const { message } = App.useApp()
  const [action, setAction] = useState('')
  const [q, setQ] = useState('')
  const [exporting, setExporting] = useState(false)
  const filters = useFilters()
  const events = useAudit({ action, q, limit: 300 })

  const exportCsv = async () => {
    setExporting(true)
    try {
      const data = await api.get<ListResult<AuditEvent>>(
        `/api/admin/audit${queryString({ action, q, limit: 1000 })}`,
      )
      if (!data.items.length) {
        message.warning('当前筛选条件下没有可导出的记录')
        return
      }
      const blob = new Blob([auditCsv(data.items)], { type: 'text/csv;charset=utf-8;' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `audit-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.csv`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      message.success(`已导出 ${data.items.length} 条审计记录`)
    } catch (error) {
      message.error((error as Error).message || '导出失败，请稍后重试')
    } finally {
      setExporting(false)
    }
  }

  const actionOptions = [
    { value: '', label: '全部动作' },
    ...(filters.data?.audit_actions ?? []).map((item) => ({ value: item, label: item })),
  ]

  const columns: ColumnsType<AuditEvent> = [
    { title: '时间', dataIndex: 'at', width: 160, render: (value: string) => formatDateTime(value) },
    {
      title: '动作',
      dataIndex: 'action',
      width: 170,
      render: (value: string) => <span className="mono">{value}</span>,
    },
    {
      title: '操作者',
      dataIndex: 'actor',
      width: 150,
      render: (value: string, row) => (
        <div>
          <div>{value || '—'}</div>
          <div className="hint">{ACTOR_LABEL[row.actor_type] ?? row.actor_type}</div>
        </div>
      ),
    },
    {
      title: '对象',
      key: 'target',
      width: 220,
      render: (_, row) => (
        <div className="mono hint" style={{ lineHeight: 1.5 }}>
          {row.serial ? <div>{row.serial}</div> : null}
          {row.key_prefix ? <div>{row.key_prefix}</div> : null}
          {row.license_id ? <div>{row.license_id}</div> : null}
          {!row.serial && !row.key_prefix && !row.license_id ? '—' : null}
        </div>
      ),
    },
    {
      title: '详情',
      dataIndex: 'detail',
      render: (value: unknown) => <span className="hint">{formatDetail(value) || '—'}</span>,
    },
  ]

  return (
    <>
      <AdminHeader
        title="审计日志"
        sub={
          events.data
            ? `匹配 ${events.data.total} 条 · 列表最多显示 300 条，按时间倒序`
            : '正在读取日志…'
        }
      >
        <Button
          icon={<DownloadOutlined />}
          onClick={exportCsv}
          loading={exporting}
          disabled={!events.data?.items.length}
        >
          导出 CSV
        </Button>
        <Button
          icon={<ReloadOutlined />}
          onClick={() => events.refetch()}
          loading={events.isFetching}
        >
          刷新
        </Button>
      </AdminHeader>

      <div className="toolbar">
        <Input.Search
          className="grow"
          allowClear
          placeholder="序列号、激活码前缀或操作者"
          onSearch={(value) => setQ(value.trim())}
          onChange={(event) => {
            if (!event.target.value) setQ('')
          }}
          style={{ maxWidth: 340 }}
        />
        <Select
          value={action}
          options={actionOptions}
          onChange={(value) => setAction(value)}
          style={{ width: 200 }}
          showSearch
          optionFilterProp="label"
        />
      </div>

      {events.isError ? <ErrorNote>{(events.error as Error).message}</ErrorNote> : null}

      <SectionCard title="事件" flush>
        <Table<AuditEvent>
          className="ps-table"
          rowKey="id"
          size="middle"
          loading={events.isFetching}
          columns={columns}
          dataSource={events.data?.items ?? []}
          pagination={TABLE_PAGINATION}
          locale={{ emptyText: '没有找到匹配的审计事件' }}
        />
      </SectionCard>
    </>
  )
}
