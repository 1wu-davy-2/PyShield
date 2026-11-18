import { Alert, App, Form, Input, Modal } from 'antd'
import { useState } from 'react'
import type { ReactNode } from 'react'

import { ApiError } from '../api/client'
import { useDeleteLicense } from '../api/hooks'
import { Callout } from './ui'

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

export function DeleteLicenseModal({
  serial,
  open,
  onClose,
  onDeleted,
}: {
  serial: string
  open: boolean
  onClose: () => void
  onDeleted: () => void
}) {
  const { message } = App.useApp()
  const remove = useDeleteLicense()
  const [form] = Form.useForm()
  const [error, setError] = useState('')

  const close = () => {
    form.resetFields()
    setError('')
    onClose()
  }

  const submit = (values: { confirm: string }) => {
    setError('')
    remove.mutate(
      { serial, confirm: String(values.confirm || '').trim() },
      {
        onSuccess: () => {
          message.success('授权及关联数据已删除')
          form.resetFields()
          onDeleted()
        },
        onError: (err) => {
          setError(err instanceof ApiError ? err.message : '删除失败，请稍后重试。')
        },
      },
    )
  }

  return (
    <Modal
      open={open}
      title="删除授权"
      okText="永久删除"
      okButtonProps={{ danger: true }}
      cancelText="取消"
      onCancel={close}
      onOk={() => form.submit()}
      confirmLoading={remove.isPending}
      destroyOnClose
    >
      <Callout tone="danger">
        将永久删除该序列号的授权、试用申请、邮箱验证码、激活记录、云端校验与扣次流水、相关订单和审计历史；客户若无其他记录则一并删除。操作不可恢复。
      </Callout>
      <div style={{ height: 14 }} />
      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 14 }} /> : null}
      <Form form={form} layout="vertical" requiredMark={false} onFinish={submit}>
        <Form.Item
          label="输入完整序列号以确认"
          name="confirm"
          rules={[
            { required: true, message: '请输入序列号' },
            {
              validator: (_, value) =>
                String(value || '').trim() === serial
                  ? Promise.resolve()
                  : Promise.reject(new Error('与序列号不一致')),
            },
          ]}
        >
          <Input className="mono" placeholder={serial} autoComplete="off" />
        </Form.Item>
      </Form>
    </Modal>
  )
}
