import { ReloadOutlined } from '@ant-design/icons'
import { Button, Input, Select, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { useKeys } from '../../api/hooks'
import type { Edition, KeyStatus, LicenseKey } from '../../api/types'
import { AdminHeader, TABLE_PAGINATION } from '../../components/admin'
import { EditionChip, ErrorNote, SectionCard, StatusTag } from '../../components/ui'
import { formatDate, formatDateTime, maskKey } from '../../lib/format'

const EDITION_OPTIONS = [
  { value: '', label: '全部版本' },
  { value: 'pro', label: '专业版' },
  { value: 'enterprise', label: '企业版' },
]

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'unused', label: '未使用' },
  { value: 'active', label: '已使用' },
  { value: 'revoked', label: '已吊销' },
  { value: 'expired', label: '已过期' },
]

export default function Keys() {
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<KeyStatus | ''>('')
  const [edition, setEdition] = useState<Edition | ''>('')
  const keys = useKeys({ q, status: status || '', edition: edition || '' })

  const columns: ColumnsType<LicenseKey> = [
    {
      title: '激活码',
      dataIndex: 'key_prefix',
      render: (value: string, row) => (
        <div>
          <Link className="mono" to={`/admin/keys/${row.id}`}>
            {value}
          </Link>
          <div className="hint mono">{maskKey(value)}</div>
        </div>
      ),
    },
    {
      title: '版本',
      dataIndex: 'edition',
      width: 96,
      render: (value: string) => <EditionChip edition={value} />,
    },
    {
      title: '状态',
      dataIndex: 'effective_status',
      width: 96,
      render: (value: string) => <StatusTag status={value} />,
    },
    {
      title: '已激活 / 上限',
      key: 'activations',
      width: 120,
      render: (_, row) => (
        <span className="mono num">
          {row.activated_count} / {row.max_activations}
        </span>
      ),
    },
    {
      title: '有效期',
      dataIndex: 'days',
      width: 96,
      render: (value: number) => <span className="num">{value} 天</span>,
    },
    {
      title: '兑换截止',
      dataIndex: 'expires_at',
      width: 120,
      render: (value: string | null) => formatDate(value),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 150,
      render: (value: string) => <span className="hint">{formatDateTime(value)}</span>,
    },
    {
      title: '',
      key: 'action',
      width: 76,
      render: (_, row) => (
        <Link className="table-link" to={`/admin/keys/${row.id}`}>
          详情
        </Link>
      ),
    },
  ]

  return (
    <>
      <AdminHeader
        title="激活码"
        sub={keys.data ? `共 ${keys.data.total} 条记录` : '正在读取激活码…'}
      >
        <Button icon={<ReloadOutlined />} onClick={() => keys.refetch()} loading={keys.isFetching}>
          刷新
        </Button>
        <Link className="ps-btn" to="/admin/keys/new">
          生成激活码
        </Link>
      </AdminHeader>

      <div className="toolbar">
        <Input.Search
          className="grow"
          allowClear
          placeholder="激活码前缀或备注"
          onSearch={(value) => setQ(value.trim())}
          onChange={(event) => {
            if (!event.target.value) setQ('')
          }}
          style={{ maxWidth: 320 }}
        />
        <Select
          value={status}
          options={STATUS_OPTIONS}
          onChange={(value) => setStatus(value as KeyStatus | '')}
          style={{ width: 128 }}
        />
        <Select
          value={edition}
          options={EDITION_OPTIONS}
          onChange={(value) => setEdition(value as Edition | '')}
          style={{ width: 128 }}
        />
      </div>

      {keys.isError ? <ErrorNote>{(keys.error as Error).message}</ErrorNote> : null}

      <SectionCard title="激活码列表" flush>
        <Table<LicenseKey>
          className="ps-table"
          rowKey="id"
          size="middle"
          loading={keys.isFetching}
          columns={columns}
          dataSource={keys.data?.items ?? []}
          pagination={TABLE_PAGINATION}
          locale={{ emptyText: '没有找到匹配的激活码' }}
        />
      </SectionCard>
    </>
  )
}
