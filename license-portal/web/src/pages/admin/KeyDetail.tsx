import { EyeOutlined } from '@ant-design/icons'
import { Alert, App, Button, Form, Input, Modal, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { useKey, useRevealKey, useRevokeKey } from '../../api/hooks'
import type { KeyActivation } from '../../api/types'
import { AdminHeader } from '../../components/admin'
import {
  Callout,
  CopyButton,
  DetailItem,
  EditionChip,
  ErrorNote,
  LoadingScreen,
  SectionCard,
  StatusTag,
} from '../../components/ui'
import { formatDate, formatDateTime } from '../../lib/format'

export default function KeyDetail() {
  const { id = '0' } = useParams()
  const keyId = Number(id)
  const { message } = App.useApp()
  const key = useKey(keyId)
  const reveal = useRevealKey()
  const revoke = useRevokeKey()
  const [revealed, setRevealed] = useState('')
  const [revokeOpen, setRevokeOpen] = useState(false)
  const [revokeForm] = Form.useForm<{ reason: string }>()

  if (key.isLoading) return <LoadingScreen text="正在读取激活码…" />
  if (key.isError || !key.data) {
    return (
      <>
        <AdminHeader title="激活码详情" sub={`#${id}`}>
          <Link className="ps-btn ghost" to="/admin/keys">
            返回列表
          </Link>
        </AdminHeader>
        <ErrorNote>{(key.error as Error)?.message ?? '激活码不存在'}</ErrorNote>
      </>
    )
  }

  const data = key.data.key
  const locked = data.effective_status === 'revoked'

  const handleReveal = () => {
    reveal.mutate(data.id, {
      onSuccess: (payload) => {
        setRevealed(payload.key)
        message.success('已显示完整激活码，本次操作已记入审计')
      },
      onError: (error) => message.error((error as Error).message),
    })
  }

  const handleRevoke = (values: { reason: string }) => {
    revoke.mutate(
      { id: data.id, reason: values.reason.trim() },
      {
        onSuccess: () => {
          message.success('激活码已吊销')
          setRevokeOpen(false)
          revokeForm.resetFields()
        },
        onError: (error) => message.error((error as Error).message),
      },
    )
  }

  const columns: ColumnsType<KeyActivation> = [
    { title: '激活时间', dataIndex: 'activated_at', width: 170, render: (value: string) => formatDateTime(value) },
    { title: '序列号', dataIndex: 'serial', render: (value: string) => <span className="mono">{value}</span> },
    { title: '机器码', dataIndex: 'machine', render: (value: string) => <span className="mono">{value}</span> },
    {
      title: '授权 ID',
      dataIndex: 'license_id',
      render: (value: string) => <span className="mono">{value}</span>,
    },
  ]

  return (
    <>
      <AdminHeader
        title={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 12 }}>
            <Link to="/admin/keys" className="icon-btn" aria-label="返回激活码列表">
              ←
            </Link>
            <span className="mono" style={{ fontSize: 20 }}>
              {data.key_prefix}
            </span>
            <StatusTag status={data.effective_status} />
          </span>
        }
        sub={`激活 ${data.activated_count} / ${data.max_activations} 台设备 · ${data.days} 天 · 创建于 ${formatDate(data.created_at)}`}
      >
        <Button icon={<EyeOutlined />} onClick={handleReveal} loading={reveal.isPending}>
          查看完整激活码
        </Button>
        <Button danger onClick={() => setRevokeOpen(true)} disabled={locked}>
          吊销
        </Button>
      </AdminHeader>

      {locked ? (
        <Callout tone="danger">
          该激活码已吊销{data.revoked_reason ? `：${data.revoked_reason}` : ''}，由它签发的授权同时失效。
        </Callout>
      ) : null}

      {revealed ? (
        <div style={{ marginBottom: 16 }}>
          <Alert
            type="warning"
            showIcon
            message="完整激活码（明文）"
            description={
              <div className="copy-row" style={{ marginTop: 6 }}>
                <code className="mono" style={{ fontSize: 14 }}>
                  {revealed}
                </code>
                <CopyButton value={revealed} label="复制" />
              </div>
            }
            closable
            onClose={() => setRevealed('')}
          />
        </div>
      ) : null}

      <SectionCard title="激活码信息">
        <div className="detail-grid">
          <DetailItem label="前缀" value={data.key_prefix} mono />
          <DetailItem label="版本" value={<EditionChip edition={data.edition} />} />
          <DetailItem label="有效天数" value={`${data.days} 天`} />
          <DetailItem label="每月构建上限" value={data.max_builds_per_month} />
          <DetailItem label="席位" value={data.seats} />
          <DetailItem label="可激活设备" value={data.max_activations} />
          <DetailItem label="宽限天数" value={`${data.grace_days} 天`} />
          <DetailItem label="兑换截止" value={formatDate(data.expires_at)} />
          <DetailItem label="创建时间" value={formatDateTime(data.created_at)} />
          <DetailItem label="创建人" value={data.created_by || '—'} />
          <DetailItem label="批次" value={data.batch_id ? `#${data.batch_id}` : '—'} />
          <DetailItem label="已激活设备" value={`${data.activated_count} / ${data.max_activations}`} />
        </div>
        {data.features.length ? (
          <>
            <div className="divider" />
            <div className="detail-label" style={{ marginBottom: 8 }}>
              features
            </div>
            <div className="feature-chips">
              {data.features.map((feature) => (
                <span className="feature-chip" key={feature}>
                  {feature}
                </span>
              ))}
            </div>
          </>
        ) : null}
        {data.notes ? (
          <>
            <div className="divider" />
            <div className="detail-label" style={{ marginBottom: 6 }}>
              备注
            </div>
            <p className="muted" style={{ fontSize: 13.5 }}>
              {data.notes}
            </p>
          </>
        ) : null}
      </SectionCard>

      <SectionCard title="已激活设备" flush>
        <Table<KeyActivation>
          className="ps-table"
          rowKey="id"
          size="middle"
          columns={columns}
          dataSource={data.activations ?? []}
          pagination={false}
          locale={{ emptyText: '这个激活码还没有被兑换' }}
        />
      </SectionCard>

      <Modal
        open={revokeOpen}
        title="吊销激活码"
        okText="确认吊销"
        okButtonProps={{ danger: true }}
        cancelText="取消"
        onCancel={() => setRevokeOpen(false)}
        onOk={() => revokeForm.submit()}
        confirmLoading={revoke.isPending}
        destroyOnClose
      >
        <Callout tone="danger">
          吊销后该激活码无法再兑换，已用它激活的授权也会同步吊销。
        </Callout>
        <div style={{ height: 14 }} />
        <Form form={revokeForm} layout="vertical" requiredMark={false} onFinish={handleRevoke}>
          <Form.Item
            label="吊销原因"
            name="reason"
            rules={[{ required: true, message: '请填写吊销原因' }]}
          >
            <Input placeholder="例如：退款、错发" maxLength={200} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
