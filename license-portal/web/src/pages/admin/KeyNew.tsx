import { DownloadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Form, Input, InputNumber, Select, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError } from '../../api/client'
import { useCreateKeys } from '../../api/hooks'
import type { CreateKeysResult, CreatedKey, Edition } from '../../api/types'
import { AdminHeader } from '../../components/admin'
import { Callout, CopyButton, EditionChip, SectionCard } from '../../components/ui'
import { applyServerErrors } from '../../lib/formErrors'
import { formatDate } from '../../lib/format'
import { useSiteConfig } from '../../hooks/useSiteConfig'

interface KeyFormValues {
  edition: Edition
  days: number
  max_builds: number
  seats: number
  max_activations: number
  grace_days: number
  quantity: number
  redemption_days: number
  batch_name?: string
  features?: string
  notes?: string
}

function downloadText(text: string, filename: string) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export default function KeyNew() {
  const { config } = useSiteConfig()
  const { message } = App.useApp()
  const createKeys = useCreateKeys()
  const [form] = Form.useForm<KeyFormValues>()
  const [result, setResult] = useState<CreateKeysResult | null>(null)
  const [error, setError] = useState('')

  const handleSubmit = (values: KeyFormValues) => {
    setError('')
    createKeys.mutate(
      {
        edition: values.edition,
        days: values.days,
        max_builds: values.max_builds,
        seats: values.seats,
        max_activations: values.max_activations,
        grace_days: values.grace_days,
        quantity: values.quantity,
        redemption_days: values.redemption_days,
        batch_name: values.batch_name ?? '',
        features: values.features ?? '',
        notes: values.notes ?? '',
      },
      {
        onSuccess: (payload) => {
          setResult(payload)
          message.success(`已生成 ${payload.keys.length} 个激活码`)
        },
        onError: (err) => {
          if (err instanceof ApiError) {
            applyServerErrors(form, err.errors)
            setError(err.message)
          } else {
            setError('生成失败，请稍后重试。')
          }
        },
      },
    )
  }

  if (result) {
    const plaintext = result.keys.map((item) => item.key).join('\n')
    const batchLabel = result.batch_id ? `batch-${result.batch_id}` : 'keys'

    const columns: ColumnsType<CreatedKey> = [
      {
        title: '激活码',
        dataIndex: 'key',
        render: (value: string, row) => (
          <div className="copy-row">
            <span className="mono">{value}</span>
            <CopyButton value={value} label="" />
            <span className="hint">{row.prefix}</span>
          </div>
        ),
      },
      { title: '版本', dataIndex: 'edition', width: 96, render: (value: string) => <EditionChip edition={value} /> },
      { title: '天数', dataIndex: 'days', width: 80, render: (value: number) => `${value} 天` },
      { title: '构建/月', dataIndex: 'max_builds', width: 96 },
      { title: '席位', dataIndex: 'seats', width: 72 },
      { title: '设备数', dataIndex: 'max_activations', width: 80 },
      { title: '兑换截止', dataIndex: 'expires_at', width: 120, render: (value: string) => formatDate(value) },
    ]

    return (
      <>
        <AdminHeader
          title="激活码已生成"
          sub={`共 ${result.keys.length} 个 · ${result.batch_id ? `批次 #${result.batch_id}` : '未建批次'}`}
        >
          <Button
            icon={<DownloadOutlined />}
            onClick={() => downloadText(plaintext, `${batchLabel}.txt`)}
          >
            下载 TXT
          </Button>
          <Button
            type="primary"
            onClick={() => {
              setResult(null)
              form.resetFields()
            }}
          >
            再生成一批
          </Button>
        </AdminHeader>

        <Alert
          type="warning"
          showIcon
          message="明文激活码只显示这一次"
          description="数据库只保存哈希与加密副本。刷新页面后只能通过「查看完整激活码」单个取回，并会记入审计日志。"
          style={{ marginBottom: 16 }}
        />

        <SectionCard
          title="生成明细"
          extra={<CopyButton value={plaintext} label="复制全部" />}
          flush
        >
          <Table<CreatedKey>
            className="ps-table"
            rowKey="id"
            size="middle"
            columns={columns}
            dataSource={result.keys}
            pagination={false}
          />
        </SectionCard>

        <SectionCard title="纯文本（便于粘贴到聊天窗口）">
          <pre className="public-key" style={{ maxHeight: 260 }}>
            {plaintext}
          </pre>
          <div className="form-actions">
            <Link className="ps-btn ghost" to="/admin/keys">
              返回激活码列表
            </Link>
          </div>
        </SectionCard>
      </>
    )
  }

  return (
    <>
      <AdminHeader title="生成激活码" sub="批量生成客户兑换用的授权激活码">
        <Link className="ps-btn ghost" to="/admin/keys">
          返回列表
        </Link>
      </AdminHeader>

      {error ? (
        <div style={{ marginBottom: 16 }}>
          <Alert type="error" showIcon message={error} />
        </div>
      ) : null}

      <div className="panel">
        <Form<KeyFormValues>
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={handleSubmit}
          disabled={createKeys.isPending}
          initialValues={{
            edition: 'pro',
            days: 365,
            max_builds: 100,
            seats: 1,
            max_activations: 1,
            grace_days: 14,
            quantity: 1,
            redemption_days: config.key_redemption_days || 30,
            batch_name: '',
            features: '',
            notes: '',
          }}
        >
          <div className="form-grid">
            <Form.Item label="版本" name="edition" rules={[{ required: true }]}>
              <Select
                options={[
                  { value: 'pro', label: '专业版（PSP）' },
                  { value: 'enterprise', label: '企业版（PSE）' },
                ]}
              />
            </Form.Item>
            <Form.Item
              label="有效天数"
              name="days"
              rules={[{ required: true, message: '请填写有效天数' }]}
              extra="1–3650 天"
            >
              <InputNumber min={1} max={3650} style={{ width: '100%' }} addonAfter="天" />
            </Form.Item>
            <Form.Item label="每月构建上限" name="max_builds" extra="0–100000">
              <InputNumber min={0} max={100000} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item label="席位" name="seats" extra="1–10000">
              <InputNumber min={1} max={10000} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item label="可激活设备数" name="max_activations" extra="1–10000">
              <InputNumber min={1} max={10000} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item label="宽限天数" name="grace_days" extra="到期后仍可使用的天数">
              <InputNumber min={0} max={3650} style={{ width: '100%' }} addonAfter="天" />
            </Form.Item>
            <Form.Item label="生成数量" name="quantity" extra="1–200">
              <InputNumber min={1} max={200} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item
              label="兑换期限"
              name="redemption_days"
              extra={`激活码需在多少天内兑换，默认 ${config.key_redemption_days} 天`}
            >
              <InputNumber min={1} max={3650} style={{ width: '100%' }} addonAfter="天" />
            </Form.Item>
            <Form.Item label="批次名称" name="batch_name" extra="可选，便于日后检索">
              <Input placeholder="例如 2026-11 微信渠道" maxLength={100} />
            </Form.Item>
            <Form.Item label="功能项" name="features" extra="留空使用版本默认功能">
              <Input placeholder="可选，逗号分隔" maxLength={200} />
            </Form.Item>
          </div>
          <Form.Item label="备注" name="notes" extra="仅管理员可见">
            <Input.TextArea rows={3} maxLength={500} placeholder="客户名称、收款方式等" />
          </Form.Item>

          <Callout tone="info">
            生成后请立即复制或下载明文激活码：页面关闭后无法批量取回，只能逐个查看并记入审计。
          </Callout>

          <div className="form-actions">
            <Button type="primary" size="large" htmlType="submit" loading={createKeys.isPending}>
              生成激活码
            </Button>
            <Link className="ps-btn ghost lg" to="/admin/keys">
              取消
            </Link>
          </div>
        </Form>
      </div>
    </>
  )
}
