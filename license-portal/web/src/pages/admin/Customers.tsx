import { ReloadOutlined } from '@ant-design/icons'
import { Button, Input, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { useCustomers } from '../../api/hooks'
import type { Customer } from '../../api/types'
import { AdminHeader, TABLE_PAGINATION } from '../../components/admin'
import { ErrorNote, SectionCard } from '../../components/ui'
import { formatDateTime } from '../../lib/format'

export default function Customers() {
  const [q, setQ] = useState('')
  const customers = useCustomers(q)

  const columns: ColumnsType<Customer> = [
    {
      title: '邮箱',
      dataIndex: 'email',
      render: (value: string, row) => (
        <Link className="mono" to={`/admin/customers/${row.id}`}>
          {value}
        </Link>
      ),
    },
    { title: '手机号', dataIndex: 'phone', width: 140, render: (value: string) => <span className="mono">{value}</span> },
    { title: '姓名', dataIndex: 'name', width: 130 },
    {
      title: '类型',
      dataIndex: 'org_type',
      width: 90,
      render: (value: string) => (value === 'company' ? '公司' : '个人'),
    },
    { title: '公司', dataIndex: 'company_name', render: (value: string) => value || '—' },
    {
      title: '授权数',
      dataIndex: 'license_count',
      width: 90,
      align: 'right',
      render: (value: number) => <span className="num">{value}</span>,
    },
    {
      title: '最近更新',
      dataIndex: 'last_license_at',
      width: 160,
      render: (value: string | null, row) => (
        <span className="hint">{formatDateTime(value || row.updated_at)}</span>
      ),
    },
    {
      title: '',
      key: 'action',
      width: 76,
      render: (_, row) => (
        <Link className="table-link" to={`/admin/customers/${row.id}`}>
          详情
        </Link>
      ),
    },
  ]

  return (
    <>
      <AdminHeader
        title="客户"
        sub={customers.data ? `共 ${customers.data.total} 位客户` : '正在读取客户列表…'}
      >
        <Button
          icon={<ReloadOutlined />}
          onClick={() => customers.refetch()}
          loading={customers.isFetching}
        >
          刷新
        </Button>
      </AdminHeader>

      <div className="toolbar">
        <Input.Search
          className="grow"
          allowClear
          placeholder="邮箱、手机号、姓名或公司"
          onSearch={(value) => setQ(value.trim())}
          onChange={(event) => {
            if (!event.target.value) setQ('')
          }}
          style={{ maxWidth: 360 }}
        />
      </div>

      {customers.isError ? <ErrorNote>{(customers.error as Error).message}</ErrorNote> : null}

      <SectionCard title="客户列表" flush>
        <Table<Customer>
          className="ps-table"
          rowKey="id"
          size="middle"
          loading={customers.isFetching}
          columns={columns}
          dataSource={customers.data?.items ?? []}
          pagination={TABLE_PAGINATION}
          locale={{ emptyText: '没有找到匹配的客户' }}
        />
      </SectionCard>
    </>
  )
}
