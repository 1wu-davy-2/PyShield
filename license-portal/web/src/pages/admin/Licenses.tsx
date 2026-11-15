import { ReloadOutlined } from '@ant-design/icons'
import { Button, Input, Select, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { useLicenses } from '../../api/hooks'
import type { Edition, LicenseStatus, LicenseSummary } from '../../api/types'
import { AdminHeader, TABLE_PAGINATION } from '../../components/admin'
import { EditionChip, ErrorNote, SectionCard, StatusTag } from '../../components/ui'
import { expiryHint, formatDate } from '../../lib/format'

const EDITION_OPTIONS = [
  { value: '', label: '全部版本' },
  { value: 'trial', label: '免费版' },
  { value: 'pro', label: '专业版' },
  { value: 'enterprise', label: '企业版' },
]

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'active', label: '生效中' },
  { value: 'expired', label: '已过期' },
  { value: 'revoked', label: '已吊销' },
]

export default function Licenses() {
  const [q, setQ] = useState('')
  const [edition, setEdition] = useState<Edition | ''>('')
  const [status, setStatus] = useState<LicenseStatus | ''>('')
  const licenses = useLicenses({ q, edition: edition || '', status: status || '' })

  const columns: ColumnsType<LicenseSummary> = [
    {
      title: '序列号',
      dataIndex: 'serial',
      render: (value: string, row) => (
        <div>
          <Link className="mono" to={`/admin/licenses/${encodeURIComponent(value)}`}>
            {value}
          </Link>
          <div className="hint mono">{[row.edition, row.license_id].filter(Boolean).join(' · ')}</div>
        </div>
      ),
    },
    {
      title: '客户',
      key: 'customer',
      render: (_, row) => (
        <div>
          <div>{row.customer_company || row.customer_name || '—'}</div>
          <div className="hint">{row.customer_email || row.customer_phone || ''}</div>
        </div>
      ),
    },
    {
      title: '版本',
      dataIndex: 'edition',
      width: 100,
      render: (value: string) => <EditionChip edition={value} />,
    },
    {
      title: '到期',
      dataIndex: 'expires',
      width: 150,
      sorter: (a, b) => String(a.expires ?? '').localeCompare(String(b.expires ?? '')),
      render: (value: string | null) => (
        <div>
          <div>{formatDate(value)}</div>
          <div className="expiry-note">{expiryHint(value)}</div>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'effective_status',
      width: 96,
      render: (value: string) => <StatusTag status={value} />,
    },
    {
      title: '修订号',
      dataIndex: 'revision',
      width: 84,
      align: 'right',
      render: (value: number) => <span className="mono num">{value}</span>,
    },
    {
      title: '',
      key: 'action',
      width: 78,
      render: (_, row) => (
        <Link className="table-link" to={`/admin/licenses/${encodeURIComponent(row.serial)}`}>
          详情
        </Link>
      ),
    },
  ]

  return (
    <>
      <AdminHeader
        title="授权"
        sub={licenses.data ? `共 ${licenses.data.total} 条记录` : '正在读取授权列表…'}
      >
        <Button icon={<ReloadOutlined />} onClick={() => licenses.refetch()} loading={licenses.isFetching}>
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
          placeholder="序列号、授权 ID、邮箱、手机号或客户名称"
          onSearch={(value) => setQ(value.trim())}
          style={{ maxWidth: 380 }}
        />
        <Select
          value={edition}
          options={EDITION_OPTIONS}
          onChange={(value) => setEdition(value as Edition | '')}
          style={{ width: 128 }}
        />
        <Select
          value={status}
          options={STATUS_OPTIONS}
          onChange={(value) => setStatus(value as LicenseStatus | '')}
          style={{ width: 128 }}
        />
      </div>

      {licenses.isError ? <ErrorNote>{(licenses.error as Error).message}</ErrorNote> : null}

      <SectionCard title="授权列表" flush>
        <Table<LicenseSummary>
          className="ps-table"
          rowKey="serial"
          size="middle"
          loading={licenses.isFetching}
          columns={columns}
          dataSource={licenses.data?.items ?? []}
          pagination={TABLE_PAGINATION}
          locale={{ emptyText: '没有找到匹配的授权' }}
        />
      </SectionCard>
    </>
  )
}
