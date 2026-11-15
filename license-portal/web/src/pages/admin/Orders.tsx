import { App, Button, Form, InputNumber, Modal, Popconfirm, Select, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { useMarkOrder, useOrders } from '../../api/hooks'
import type { Order, OrderStatus } from '../../api/types'
import { AdminHeader, TABLE_PAGINATION } from '../../components/admin'
import { Callout, EditionChip, ErrorNote, SectionCard, StatusTag } from '../../components/ui'
import { formatDateTime } from '../../lib/format'

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待处理' },
  { value: 'paid', label: '已收款' },
  { value: 'fulfilled', label: '已完成' },
  { value: 'cancelled', label: '已取消' },
]

export default function Orders() {
  const { message } = App.useApp()
  const [status, setStatus] = useState<OrderStatus | ''>('pending')
  const orders = useOrders(status || '')
  const mark = useMarkOrder()
  const [paidOrder, setPaidOrder] = useState<Order | null>(null)
  const [amountForm] = Form.useForm<{ amount?: number | string | null }>()

  const update = (order: Order, next: OrderStatus, amount?: string) => {
    mark.mutate(
      { id: order.id, status: next, amount },
      {
        onSuccess: () => message.success('订单状态已更新'),
        onError: (error) => message.error((error as Error).message),
      },
    )
  }

  const columns: ColumnsType<Order> = [
    { title: 'ID', dataIndex: 'id', width: 64, render: (value: number) => <span className="mono">#{value}</span> },
    { title: '时间', dataIndex: 'created_at', width: 150, render: (value: string) => <span className="hint">{formatDateTime(value)}</span> },
    {
      title: '版本',
      dataIndex: 'edition',
      width: 100,
      render: (value: string) => <EditionChip edition={value} />,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 96,
      render: (value: string) => <StatusTag status={value} />,
    },
    { title: '联系方式', dataIndex: 'contact', width: 150 },
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
      title: '金额',
      dataIndex: 'amount',
      width: 96,
      render: (value: string) => (value ? <span className="num">¥{value}</span> : '—'),
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      render: (_, row) => (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <Button size="small" onClick={() => setPaidOrder(row)}>
            已收款
          </Button>
          <Popconfirm
            title="标记为已完成？"
            description="确认已发码并完成交付。"
            onConfirm={() => update(row, 'fulfilled')}
          >
            <Button size="small">完成</Button>
          </Popconfirm>
          <Popconfirm title="取消这笔订单？" onConfirm={() => update(row, 'cancelled')}>
            <Button size="small" danger>
              取消
            </Button>
          </Popconfirm>
        </div>
      ),
    },
  ]

  const submitAmount = (values: { amount?: number | string | null }) => {
    if (!paidOrder) return
    update(paidOrder, 'paid', values.amount === null || values.amount === undefined ? '' : String(values.amount))
    setPaidOrder(null)
    amountForm.resetFields()
  }

  return (
    <>
      <AdminHeader
        title="订单"
        sub={orders.data ? `共 ${orders.data.total} 条记录 · 人工收款后在此标记并发码` : '正在读取订单…'}
      />

      <div className="toolbar">
        <Select
          value={status}
          options={STATUS_OPTIONS}
          onChange={(value) => setStatus(value as OrderStatus | '')}
          style={{ width: 140 }}
        />
      </div>

      <Callout tone="info">
        当前为人工收款流程：确认微信到账后点「已收款」记录金额，生成激活码发给客户，再点「完成」结单。
      </Callout>

      {orders.isError ? (
        <div style={{ marginTop: 14 }}>
          <ErrorNote>{(orders.error as Error).message}</ErrorNote>
        </div>
      ) : null}

      <SectionCard title="订单列表" flush>
        <Table<Order>
          className="ps-table"
          rowKey="id"
          size="middle"
          loading={orders.isFetching}
          columns={columns}
          dataSource={orders.data?.items ?? []}
          pagination={TABLE_PAGINATION}
          locale={{ emptyText: '没有找到匹配的订单' }}
        />
      </SectionCard>

      <Modal
        open={Boolean(paidOrder)}
        title={`标记已收款 · 订单 #${paidOrder?.id ?? ''}`}
        okText="保存"
        cancelText="取消"
        onCancel={() => setPaidOrder(null)}
        onOk={() => amountForm.submit()}
        confirmLoading={mark.isPending}
        destroyOnClose
      >
        <p className="hint" style={{ marginBottom: 14 }}>
          金额仅用于对账，可留空。
        </p>
        <Form
          form={amountForm}
          layout="vertical"
          requiredMark={false}
          onFinish={submitAmount}
          initialValues={{ amount: paidOrder?.amount ?? '' }}
        >
          <Form.Item label="实收金额（元）" name="amount">
            <InputNumber min={0} precision={2} style={{ width: '100%' }} placeholder="例如 699" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
