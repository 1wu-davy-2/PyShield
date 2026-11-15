import { Input, Select, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'

import { useAudit, useFilters } from '../../api/hooks'
import type { AuditEvent } from '../../api/types'
import { AdminHeader, TABLE_PAGINATION } from '../../components/admin'
import { ErrorNote, SectionCard } from '../../components/ui'
import { formatDateTime, formatDetail } from '../../lib/format'

const ACTOR_LABEL: Record<string, string> = {
  admin: '管理员',
  client: '客户端',
  public: '访客',
  system: '系统',
}

export default function Audit() {
  const [action, setAction] = useState('')
  const [q, setQ] = useState('')
  const filters = useFilters()
  const events = useAudit({ action, q, limit: 300 })

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
        sub={events.data ? `最近 ${events.data.total} 条记录，按时间倒序` : '正在读取日志…'}
      />

      <div className="toolbar">
        <Input.Search
          className="grow"
          allowClear
          placeholder="序列号、激活码前缀或操作者"
          onSearch={(value) => setQ(value.trim())}
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
