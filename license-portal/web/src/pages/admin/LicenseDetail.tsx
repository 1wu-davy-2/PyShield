import { Alert, App, Button, Checkbox, DatePicker, Form, Input, InputNumber, Modal, Select, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError } from '../../api/client'
import {
  useLicense,
  useRenewLicense,
  useRevokeLicense,
  useSetBuildQuota,
  useUpgradeLicense,
} from '../../api/hooks'
import type { AuditEvent, ClientCheck, KeyActivation } from '../../api/types'
import { AdminHeader, TABLE_PAGINATION } from '../../components/admin'
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
import { applyServerErrors } from '../../lib/formErrors'
import { formatDate, formatDateTime, formatDetail, remainingText } from '../../lib/format'

type ModalKind = 'renew' | 'upgrade' | 'revoke' | 'quota' | null

export default function LicenseDetail() {
  const { serial = '' } = useParams()
  const { message } = App.useApp()
  const license = useLicense(serial)
  const renew = useRenewLicense()
  const upgrade = useUpgradeLicense()
  const revoke = useRevokeLicense()
  const quota = useSetBuildQuota()
  const [modal, setModal] = useState<ModalKind>(null)
  const [formError, setFormError] = useState('')
  const [renewForm] = Form.useForm()
  const [upgradeForm] = Form.useForm()
  const [revokeForm] = Form.useForm()
  const [quotaForm] = Form.useForm()

  if (license.isLoading) return <LoadingScreen text="正在读取授权详情…" />
  if (license.isError || !license.data) {
    return (
      <>
        <AdminHeader title="授权详情" sub={serial}>
          <Link className="ps-btn ghost" to="/admin/licenses">
            返回列表
          </Link>
        </AdminHeader>
        <ErrorNote>{(license.error as Error)?.message ?? '授权不存在'}</ErrorNote>
      </>
    )
  }

  const data = license.data.license
  const locked = data.effective_status === 'revoked'

  const closeModal = () => {
    setModal(null)
    setFormError('')
  }

  const handleError = (error: unknown) => {
    if (error instanceof ApiError) {
      const form =
        modal === 'renew'
          ? renewForm
          : modal === 'upgrade'
            ? upgradeForm
            : modal === 'quota'
              ? quotaForm
              : revokeForm
      applyServerErrors(form, error.errors)
      setFormError(error.message)
    } else {
      setFormError('操作失败，请稍后重试。')
    }
  }

  const submitRenew = (values: Record<string, unknown>) => {
    setFormError('')
    renew.mutate(
      {
        serial: data.serial,
        body: {
          days: values.days as number,
          expires: values.expires ? (values.expires as { format: (f: string) => string }).format('YYYY-MM-DD') : '',
          max_builds: (values.max_builds as number | null) ?? null,
          grace_days: (values.grace_days as number | null) ?? null,
          notes: (values.notes as string) ?? '',
        },
      },
      {
        onSuccess: () => {
          message.success('续费已保存')
          closeModal()
        },
        onError: handleError,
      },
    )
  }

  const submitUpgrade = (values: Record<string, unknown>) => {
    setFormError('')
    upgrade.mutate(
      {
        serial: data.serial,
        body: {
          edition: values.edition as string,
          days: values.days as number,
          expires: values.expires ? (values.expires as { format: (f: string) => string }).format('YYYY-MM-DD') : '',
          seats: (values.seats as number | null) ?? null,
          max_builds: (values.max_builds as number | null) ?? null,
          notes: (values.notes as string) ?? '',
        },
      },
      {
        onSuccess: () => {
          message.success('版本已更新，revision 已提升')
          closeModal()
        },
        onError: handleError,
      },
    )
  }

  const submitRevoke = (values: { reason: string }) => {
    setFormError('')
    revoke.mutate(
      { serial: data.serial, body: { reason: values.reason.trim() } },
      {
        onSuccess: () => {
          message.success('授权已吊销')
          closeModal()
        },
        onError: handleError,
      },
    )
  }

  const submitQuota = (values: { limit?: number | null; reset?: boolean }) => {
    setFormError('')
    quota.mutate(
      {
        serial: data.serial,
        limit: values.limit ?? null,
        reset: Boolean(values.reset),
      },
      {
        onSuccess: () => {
          message.success('构建配额已更新')
          closeModal()
        },
        onError: handleError,
      },
    )
  }

  const activationColumns: ColumnsType<KeyActivation> = [
    {
      title: '激活时间',
      dataIndex: 'activated_at',
      width: 170,
      render: (value: string) => formatDateTime(value),
    },
    { title: '序列号', dataIndex: 'serial', render: (value: string) => <span className="mono">{value}</span> },
    { title: '机器码', dataIndex: 'machine', render: (value: string) => <span className="mono">{value}</span> },
    {
      title: '授权 ID',
      dataIndex: 'license_id',
      render: (value: string) => <span className="mono">{value}</span>,
    },
  ]

  const eventColumns: ColumnsType<AuditEvent> = [
    { title: '时间', dataIndex: 'at', width: 170, render: (value: string) => formatDateTime(value) },
    { title: '动作', dataIndex: 'action', width: 150 },
    { title: '操作者', dataIndex: 'actor', width: 130 },
    {
      title: '详情',
      dataIndex: 'detail',
      render: (value: unknown) => <span className="hint">{formatDetail(value) || '—'}</span>,
    },
  ]

  const checkColumns: ColumnsType<ClientCheck> = [
    { title: '时间', dataIndex: 'at', width: 170, render: (value: string) => formatDateTime(value) },
    {
      title: '类型',
      dataIndex: 'kind',
      width: 110,
      render: (value: string) =>
        ({ verify: '心跳校验', preflight: '构建预检', consume: '构建扣次' })[value] ?? value,
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
      title: '详情',
      dataIndex: 'detail',
      render: (value: unknown) => <span className="hint">{formatDetail(value) || '—'}</span>,
    },
  ]

  const effectiveLimit = data.builds_limit ?? data.max_builds_per_month
  const quotaPeriodText = data.edition === 'trial' ? '试用总量（不重置）' : '按月重置'

  return (
    <>
      <AdminHeader
        title={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 12 }}>
            <Link to="/admin/licenses" className="icon-btn" aria-label="返回授权列表">
              ←
            </Link>
            <span className="mono" style={{ fontSize: 20 }}>
              {data.serial}
            </span>
            <StatusTag status={data.effective_status} />
          </span>
        }
        sub={`${data.license_id} · revision ${data.revision} · ${data.source || 'trial'}`}
      >
        <Button onClick={() => setModal('renew')} disabled={locked}>
          续费
        </Button>
        <Button type="primary" onClick={() => setModal('upgrade')} disabled={locked}>
          升级版本
        </Button>
        <Button onClick={() => setModal('quota')}>调整构建配额</Button>
        <Button danger onClick={() => setModal('revoke')} disabled={locked}>
          吊销
        </Button>
      </AdminHeader>

      {locked ? (
        <Callout tone="danger">
          该授权已吊销{data.revoked_reason ? `：${data.revoked_reason}` : ''}，客户端刷新后将拒绝使用。
        </Callout>
      ) : null}
      {!locked && data.effective_status === 'expired' ? (
        <Callout tone="danger">
          {data.grace_days > 0
            ? `该授权已过期，${data.grace_days} 天宽限期也已结束，客户端刷新后将拒绝使用。`
            : '该授权已过期，客户端刷新后将拒绝使用。'}
        </Callout>
      ) : null}

      <SectionCard title="授权信息">
        <div className="detail-grid">
          <DetailItem label="授权 ID" value={data.license_id} mono />
          <DetailItem label="版本" value={<EditionChip edition={data.edition} />} />
          <DetailItem label="序列号" value={<span className="mono">{data.serial}</span>} />
          <DetailItem label="机器码" value={<span className="mono">{data.machine}</span>} />
          <DetailItem
            label="客户"
            value={
              <div>
                <div>{data.customer_company || data.customer_name || '—'}</div>
                <div className="hint">
                  {[data.customer_email, data.customer_phone].filter(Boolean).join(' · ')}
                </div>
              </div>
            }
          />
          <DetailItem
            label="到期日"
            value={
              <div>
                <div>{formatDate(data.expires)}</div>
                <div className="expiry-note">{remainingText(data.expires)}</div>
              </div>
            }
          />
          <DetailItem label="宽限天数" value={`${data.grace_days} 天`} />
          <DetailItem label="每月构建上限" value={`${data.max_builds_per_month} 次`} />
          <DetailItem label="席位" value={data.seats} />
          <DetailItem label="可激活设备" value={data.max_activations} />
          <DetailItem label="修订号" value={data.revision} />
          <DetailItem label="最近更新" value={formatDateTime(data.updated_at)} />
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
        <div className="divider" />
        <div className="row-between">
          <p className="hint" style={{ maxWidth: '58ch' }}>
            修改授权会重新签名并提升 revision，客户端下次刷新时自动领取新文档。
          </p>
          <CopyButton value={data.serial} label="复制序列号" />
        </div>
      </SectionCard>

      <SectionCard title="云端构建配额">
        <div className="detail-grid">
          <DetailItem
            label="本期已用"
            value={`${data.builds_used ?? 0} 次`}
          />
          <DetailItem
            label="本期上限"
            value={effectiveLimit === 0 ? '不限次数' : `${effectiveLimit} 次`}
          />
          <DetailItem
            label="配额来源"
            value={data.builds_limit === null || data.builds_limit === undefined ? '版本默认' : '管理员单独设置'}
          />
          <DetailItem label="计数周期" value={`${quotaPeriodText}${data.builds_period ? ` · ${data.builds_period}` : ''}`} />
          <DetailItem label="最近心跳" value={data.last_seen_at ? formatDateTime(data.last_seen_at) : '尚未上报'} />
        </div>
        <div className="divider" />
        <p className="hint" style={{ maxWidth: '64ch' }}>
          客户端每次构建前会向云端同步扣减次数；心跳校验每 1–5 分钟随机上报。0 表示不限次数，留空表示跟随版本默认上限。
        </p>
      </SectionCard>

      <SectionCard title="最近云端校验" flush>
        <Table<ClientCheck>
          className="ps-table"
          rowKey="id"
          size="middle"
          columns={checkColumns}
          dataSource={data.client_checks ?? []}
          pagination={false}
          locale={{ emptyText: '暂无云端校验记录' }}
        />
      </SectionCard>

      <SectionCard title="激活记录" flush>
        <Table<KeyActivation>
          className="ps-table"
          rowKey="id"
          size="middle"
          columns={activationColumns}
          dataSource={data.activations}
          pagination={false}
          locale={{ emptyText: '暂无激活记录' }}
        />
      </SectionCard>

      <SectionCard title="最近事件" flush>
        <Table<AuditEvent>
          className="ps-table"
          rowKey="id"
          size="middle"
          columns={eventColumns}
          dataSource={data.events}
          pagination={data.events.length > 20 ? TABLE_PAGINATION : false}
          locale={{ emptyText: '暂无审计事件' }}
        />
      </SectionCard>

      <Modal
        open={modal === 'renew'}
        title="续费"
        okText="保存续费"
        cancelText="取消"
        onCancel={closeModal}
        onOk={() => renewForm.submit()}
        confirmLoading={renew.isPending}
        destroyOnClose
      >
        <p className="hint" style={{ marginBottom: 16 }}>
          不填写到期日时，从当前到期日（已过期则从今天）顺延。
        </p>
        {formError ? <Alert type="error" showIcon message={formError} style={{ marginBottom: 14 }} /> : null}
        <Form
          form={renewForm}
          layout="vertical"
          requiredMark={false}
          onFinish={submitRenew}
          initialValues={{
            days: 365,
            max_builds: data.max_builds_per_month,
            grace_days: data.grace_days,
            notes: '',
          }}
        >
          <Form.Item label="续费天数" name="days" rules={[{ required: true, message: '请填写天数' }]}>
            <InputNumber min={1} max={3650} style={{ width: '100%' }} addonAfter="天" />
          </Form.Item>
          <Form.Item label="指定到期日" name="expires" extra="可选，填写后忽略续费天数">
            <DatePicker style={{ width: '100%' }} placeholder="YYYY-MM-DD" />
          </Form.Item>
          <Form.Item label="每月构建上限" name="max_builds">
            <InputNumber min={0} max={100000} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="宽限天数" name="grace_days">
            <InputNumber min={0} max={3650} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="备注" name="notes">
            <Input.TextArea rows={2} maxLength={500} placeholder="续费说明（可选）" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={modal === 'upgrade'}
        title="升级 / 变更版本"
        okText="保存升级"
        cancelText="取消"
        onCancel={closeModal}
        onOk={() => upgradeForm.submit()}
        confirmLoading={upgrade.isPending}
        destroyOnClose
      >
        <p className="hint" style={{ marginBottom: 16 }}>
          切换版本会重新签发授权文档；从免费版升级时，有效期从今天开始计算。
        </p>
        {formError ? <Alert type="error" showIcon message={formError} style={{ marginBottom: 14 }} /> : null}
        <Form
          form={upgradeForm}
          layout="vertical"
          requiredMark={false}
          onFinish={submitUpgrade}
          initialValues={{
            edition: data.edition === 'enterprise' ? 'enterprise' : 'pro',
            days: 365,
            seats: data.seats,
            max_builds: data.max_builds_per_month,
            notes: '',
          }}
        >
          <Form.Item label="目标版本" name="edition" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'pro', label: '专业版' },
                { value: 'enterprise', label: '企业版' },
              ]}
            />
          </Form.Item>
          <Form.Item label="有效天数" name="days" rules={[{ required: true, message: '请填写天数' }]}>
            <InputNumber min={1} max={3650} style={{ width: '100%' }} addonAfter="天" />
          </Form.Item>
          <Form.Item label="席位" name="seats">
            <InputNumber min={1} max={10000} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="每月构建上限" name="max_builds">
            <InputNumber min={0} max={100000} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="备注" name="notes">
            <Input.TextArea rows={2} maxLength={500} placeholder="升级说明（可选）" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={modal === 'quota'}
        title="调整构建配额"
        okText="保存配额"
        cancelText="取消"
        onCancel={closeModal}
        onOk={() => quotaForm.submit()}
        confirmLoading={quota.isPending}
        destroyOnClose
      >
        <p className="hint" style={{ marginBottom: 16 }}>
          单独设置会覆盖版本默认的构建上限；0 表示不限次数，留空表示恢复版本默认。
        </p>
        {formError ? <Alert type="error" showIcon message={formError} style={{ marginBottom: 14 }} /> : null}
        <Form
          form={quotaForm}
          layout="vertical"
          requiredMark={false}
          onFinish={submitQuota}
          initialValues={{ limit: data.builds_limit ?? null, reset: false }}
        >
          <Form.Item label="构建次数上限" name="limit" extra={`当前版本默认：${data.max_builds_per_month} 次`}>
            <InputNumber min={0} max={1000000} style={{ width: '100%' }} placeholder="留空 = 跟随版本默认" />
          </Form.Item>
          <Form.Item name="reset" valuePropName="checked">
            <Checkbox>同时将本期已用次数清零</Checkbox>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={modal === 'revoke'}
        title="吊销授权"
        okText="确认吊销"
        okButtonProps={{ danger: true }}
        cancelText="取消"
        onCancel={closeModal}
        onOk={() => revokeForm.submit()}
        confirmLoading={revoke.isPending}
        destroyOnClose
      >
        <Callout tone="danger">
          吊销后客户端刷新即拒绝使用，且不可恢复。请确认已完成退款或违规核实。
        </Callout>
        <div style={{ height: 14 }} />
        {formError ? <Alert type="error" showIcon message={formError} style={{ marginBottom: 14 }} /> : null}
        <Form form={revokeForm} layout="vertical" requiredMark={false} onFinish={submitRevoke}>
          <Form.Item
            label="吊销原因"
            name="reason"
            rules={[{ required: true, message: '请填写吊销原因' }]}
          >
            <Input placeholder="例如：退款、违规使用" maxLength={200} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
