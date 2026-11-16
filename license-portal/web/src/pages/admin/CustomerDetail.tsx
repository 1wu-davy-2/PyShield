import { ReloadOutlined } from '@ant-design/icons'
import { Button, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { Link, useParams } from 'react-router-dom'

import { useCustomer } from '../../api/hooks'
import type { CustomerApplication, LicenseSummary, Order } from '../../api/types'
import { AdminHeader, TABLE_PAGINATION } from '../../components/admin'
import {
  DetailItem,
  EditionChip,
  ErrorNote,
  LoadingScreen,
  SectionCard,
  StatusTag,
} from '../../components/ui'
import {
  expiryHint,
  formatDate,
  formatDateTime,
  shortMachine,
} from '../../lib/format'

export default function CustomerDetail() {
  const { id = '' } = useParams()
  const customerId = Number(id)
  const query = useCustomer(customerId)

  if (!customerId) {
    return (
      <>
        <AdminHeader title="客户详情" sub="客户编号无效">
          <Link className="ps-btn ghost" to="/admin/customers">
            返回客户列表
          </Link>
        </AdminHeader>
        <ErrorNote>请从客户列表进入详情页。</ErrorNote>
      </>
    )
  }

  if (query.isLoading) return <LoadingScreen text="正在读取客户信息…" />
  if (query.isError || !query.data) {
    return (
      <>
        <AdminHeader title="客户详情" sub={`#${customerId}`}>
          <Link className="ps-btn ghost" to="/admin/customers">
            返回客户列表
          </Link>
        </AdminHeader>
        <ErrorNote>{(query.error as Error)?.message ?? '客户不存在'}</ErrorNote>
      </>
    )
  }

  const { customer, licenses, orders, applications } = query.data

  const licenseColumns: ColumnsType<LicenseSummary> = [
    {
      title: '序列号',
      dataIndex: 'serial',
      render: (value: string, row) => (
        <div>
          <Link className="mono" to={`/admin/licenses/${encodeURIComponent(value)}`}>
            {value}
          </Link>
          <div className="hint mono">{row.license_id}</div>
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
      title: '状态',
      dataIndex: 'effective_status',
      width: 96,
      render: (value: string) => <StatusTag status={value} />,
    },
    {
      title: '到期',
      dataIndex: 'expires',
      width: 160,
      render: (value: string | null) => (
        <div>
          <div>{formatDate(value)}</div>
          <div className="expiry-note">{expiryHint(value)}</div>
        </div>
      ),
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 90,
      render: (value: string) => (value === 'trial' ? '试用' : value === 'admin' ? '后台开通' : '激活码'),
    },
    {
      title: '修订号',
      dataIndex: 'revision',
      width: 84,
      align: 'right',
      render: (value: number) => <span className="mono num">{value}</span>,
    },
  ]

  const orderColumns: ColumnsType<Order> = [
    {
      title: '订单',
      dataIndex: 'id',
      width: 80,
      render: (value: number) => <span className="mono">#{value}</span>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (value: string) => <StatusTag status={value} />,
    },
    {
      title: '版本',
      dataIndex: 'edition',
      width: 100,
      render: (value: string) => <EditionChip edition={value} />,
    },
    {
      title: '序列号',
      dataIndex: 'serial',
      render: (value: string) =>
        value ? (
          <Link className="mono" to={`/admin/licenses/${encodeURIComponent(value)}`}>
            {value}
          </Link>
        ) : (
          '—'
        ),
    },
    { title: '联系方式', dataIndex: 'contact' },
    {
      title: '金额',
      dataIndex: 'amount',
      width: 100,
      render: (value: string) => (value ? <span className="num">¥{value}</span> : '—'),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 160,
      render: (value: string) => <span className="hint">{formatDateTime(value)}</span>,
    },
  ]

  const applicationColumns: ColumnsType<CustomerApplication> = [
    {
      title: '申请',
      dataIndex: 'id',
      width: 80,
      render: (value: number) => <span className="mono">#{value}</span>,
    },
    {
      title: '序列号',
      dataIndex: 'serial',
      render: (value: string) => <span className="mono">{value}</span>,
    },
    {
      title: '机器码',
      dataIndex: 'machine',
      width: 180,
      render: (value: string) => <span className="mono">{shortMachine(value, 12)}</span>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      render: (value: string) => <StatusTag status={value} />,
    },
    {
      title: '验证码有效期',
      dataIndex: 'code_expires',
      width: 160,
      render: (value: string | null) => <span className="hint">{formatDateTime(value)}</span>,
    },
    {
      title: '提交时间',
      dataIndex: 'created_at',
      width: 160,
      render: (value: string) => <span className="hint">{formatDateTime(value)}</span>,
    },
  ]

  return (
    <>
      <AdminHeader
        title={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 12 }}>
            <Link to="/admin/customers" className="icon-btn" aria-label="返回客户列表">
              ←
            </Link>
            <span>{customer.company_name || customer.name}</span>
          </span>
        }
        sub={`${customer.email} · ${customer.phone}`}
      >
        <Button
          icon={<ReloadOutlined />}
          onClick={() => query.refetch()}
          loading={query.isFetching}
        >
          刷新
        </Button>
        <Link className="ps-btn ghost" to="/admin/customers">
          返回列表
        </Link>
      </AdminHeader>

      <SectionCard title="客户信息">
        <div className="detail-grid">
          <DetailItem label="邮箱" value={<span className="mono">{customer.email}</span>} />
          <DetailItem label="手机号" value={<span className="mono">{customer.phone}</span>} />
          <DetailItem label="姓名" value={customer.name} />
          <DetailItem label="主体类型" value={customer.org_type === 'company' ? '公司' : '个人'} />
          <DetailItem label="公司" value={customer.company_name || '—'} />
          <DetailItem label="授权数" value={customer.license_count} />
          <DetailItem label="同意条款时间" value={formatDateTime(customer.consent_at)} />
          <DetailItem label="创建时间" value={formatDateTime(customer.created_at)} />
          <DetailItem label="最近更新" value={formatDateTime(customer.updated_at)} />
          <DetailItem
            label="最近授权更新"
            value={formatDateTime(customer.last_license_at)}
          />
        </div>
      </SectionCard>

      <SectionCard title="授权" flush>
        <Table<LicenseSummary>
          className="ps-table"
          rowKey="serial"
          size="middle"
          columns={licenseColumns}
          dataSource={licenses}
          pagination={licenses.length > 20 ? TABLE_PAGINATION : false}
          locale={{ emptyText: '该客户暂无授权' }}
        />
      </SectionCard>

      <SectionCard title="订单" flush>
        <Table<Order>
          className="ps-table"
          rowKey="id"
          size="middle"
          columns={orderColumns}
          dataSource={orders}
          pagination={orders.length > 20 ? TABLE_PAGINATION : false}
          locale={{ emptyText: '该客户暂无订单' }}
        />
      </SectionCard>

      <SectionCard title="试用申请" flush>
        <Table<CustomerApplication>
          className="ps-table"
          rowKey="id"
          size="middle"
          columns={applicationColumns}
          dataSource={applications}
          pagination={applications.length > 20 ? TABLE_PAGINATION : false}
          locale={{ emptyText: '该客户暂无试用申请' }}
        />
      </SectionCard>
    </>
  )
}
