import { ReloadOutlined } from '@ant-design/icons'
import { Button, Input, Select, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { useClientChecks } from '../../api/hooks'
import type { ClientCheck } from '../../api/types'
import { AdminHeader, TABLE_PAGINATION } from '../../components/admin'
import { ErrorNote, SectionCard } from '../../components/ui'
import { formatDateTime, formatDetail } from '../../lib/format'

const KIND_LABEL: Record<string, string> = {
  verify: '心跳校验',
  preflight: '构建预检',
  consume: '构建扣次',
}

export default function ClientChecks() {
  const [serial, setSerial] = useState('')
  const [kind, setKind] = useState('')
  const [ok, setOk] = useState('')
  const checks = useClientChecks({ serial, kind, ok, limit: 300 })

  const columns: ColumnsType<ClientCheck> = [
    { title: '时间', dataIndex: 'at', width: 160, render: (value: string) => formatDateTime(value) },
    {
      title: '类型',
      dataIndex: 'kind',
      width: 110,
      render: (value: string) => KIND_LABEL[value] ?? value,
    },
    {
      title: '结果',
      dataIndex: 'ok',
      width: 170,
      render: (value: number, row) => (
        <span>
          <Tag color={value ? 'green' : 'red'}>{value ? '通过' : '拒绝'}</Tag>
          {row.code ? <span className="mono hint">{row.code}</span> : null}
        </span>
      ),
    },
    {
      title: '序列号',
      dataIndex: 'serial',
      width: 200,
      render: (value: string) =>
        value ? (
          <Link className="mono" to={`/admin/licenses/${encodeURIComponent(value)}`}>
            {value}
          </Link>
        ) : (
          '—'
        ),
    },
    {
      title: '授权 ID',
      dataIndex: 'license_id',
      width: 230,
      render: (value: string) => <span className="mono hint">{value || '—'}</span>,
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
        title="云端校验"
        sub={
          checks.data
            ? `匹配 ${checks.data.total} 条 · 列表最多显示 300 条，按时间倒序`
            : '正在读取校验记录…'
        }
      >
        <Button
          icon={<ReloadOutlined />}
          onClick={() => checks.refetch()}
          loading={checks.isFetching}
        >
          刷新
        </Button>
      </AdminHeader>

      <div className="toolbar">
        <Input.Search
          className="grow"
          allowClear
          placeholder="按序列号搜索"
          onSearch={(value) => setSerial(value.trim())}
          onChange={(event) => {
            if (!event.target.value) setSerial('')
          }}
          style={{ maxWidth: 320 }}
        />
        <Select
          value={kind}
          onChange={(value) => setKind(value)}
          style={{ width: 160 }}
          options={[
            { value: '', label: '全部类型' },
            { value: 'verify', label: '心跳校验' },
            { value: 'preflight', label: '构建预检' },
            { value: 'consume', label: '构建扣次' },
          ]}
        />
        <Select
          value={ok}
          onChange={(value) => setOk(value)}
          style={{ width: 140 }}
          options={[
            { value: '', label: '全部结果' },
            { value: '1', label: '通过' },
            { value: '0', label: '拒绝' },
          ]}
        />
      </div>

      {checks.isError ? <ErrorNote>{(checks.error as Error).message}</ErrorNote> : null}

      <SectionCard title="客户端校验记录" flush>
        <Table<ClientCheck>
          className="ps-table"
          rowKey="id"
          size="middle"
          loading={checks.isFetching}
          columns={columns}
          dataSource={checks.data?.items ?? []}
          pagination={TABLE_PAGINATION}
          locale={{ emptyText: '暂无云端校验记录' }}
        />
      </SectionCard>
    </>
  )
}