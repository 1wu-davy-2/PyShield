import { App, Button, Form, Input } from 'antd'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError } from '../../api/client'
import { useRedeemKey } from '../../api/hooks'
import type { PublicLicense } from '../../api/types'
import { Callout, MetaList, SectionCard, StatusTag } from '../../components/ui'
import { applyServerErrors } from '../../lib/formErrors'
import { editionLabel, expiryHint, formatDate, shortMachine } from '../../lib/format'
import { useSiteConfig } from '../../hooks/useSiteConfig'

interface RedeemValues {
  key: string
  serial: string
  machine: string
}

const keyValidator = {
  validator: (_rule: unknown, value: string) => {
    const cleaned = String(value ?? '')
      .toUpperCase()
      .replace(/[^A-Z0-9]/g, '')
    if (!cleaned) return Promise.reject(new Error('请输入激活码'))
    if (cleaned.startsWith('PST')) {
      return Promise.reject(new Error('PST 开头的是试用码，无需在这里兑换'))
    }
    if (!/^PS[PE][0-9A-Z]{16}$/.test(cleaned)) {
      return Promise.reject(new Error('激活码格式不正确，应为 PSP/PSE 开头共 16 位'))
    }
    return Promise.resolve()
  },
}

const serialValidator = {
  validator: (_rule: unknown, value: string) => {
    const text = String(value ?? '').trim().toUpperCase()
    if (!text) return Promise.reject(new Error('请输入序列号'))
    if (!/^PS-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(text)) {
      return Promise.reject(new Error('序列号格式不正确，例如 PS-XXXX-XXXX-XXXX'))
    }
    return Promise.resolve()
  },
}

const machineValidator = {
  validator: (_rule: unknown, value: string) => {
    const text = String(value ?? '').trim()
    if (!text) return Promise.reject(new Error('请输入机器码'))
    if (!/^[0-9a-fA-F]{32}$/.test(text)) {
      return Promise.reject(new Error('机器码应为 32 位十六进制'))
    }
    return Promise.resolve()
  },
}

export default function Redeem() {
  const [params] = useSearchParams()
  const { config } = useSiteConfig()
  const { message } = App.useApp()
  const redeem = useRedeemKey()
  const [form] = Form.useForm<RedeemValues>()
  const [result, setResult] = useState<PublicLicense | null>(null)
  const [error, setError] = useState('')

  const handleSubmit = (values: RedeemValues) => {
    setError('')
    redeem.mutate(
      {
        key: values.key.trim(),
        serial: values.serial.trim().toUpperCase(),
        machine: values.machine.trim().toLowerCase(),
      },
      {
        onSuccess: (payload) => {
          setResult(payload)
          message.success('激活码兑换成功')
        },
        onError: (err) => {
          if (err instanceof ApiError) {
            applyServerErrors(form, err.errors)
            setError(err.message)
          } else {
            setError('兑换失败，请稍后重试。')
          }
        },
      },
    )
  }

  if (result) {
    return (
      <div className="shell page">
        <div className="page-head">
          <h1 className="page-title">激活码已生效</h1>
          <p className="page-lead">
            授权已写入授权中心。回到 PyShield 客户端点击「刷新授权状态」即可领取。
          </p>
        </div>
        <div className="split">
          <div className="panel">
            <div className="row-between" style={{ marginBottom: 14 }}>
              <strong style={{ fontSize: 16 }}>{editionLabel(result.edition)}</strong>
              <StatusTag status="active" label="已生效" />
            </div>
            <MetaList
              items={[
                ['序列号', <span className="mono">{result.serial}</span>],
                ['机器码', <span className="mono">{shortMachine(result.machine)}</span>],
                ['授权 ID', <span className="mono">{result.license_id}</span>],
                ['到期日', formatDate(result.expires)],
                ['剩余', expiryHint(result.expires) || '—'],
                ['每月构建', `${result.max_builds ?? '—'} 次`],
                ['revision', String(result.revision)],
              ]}
            />
            <div className="form-actions">
              <Link className="ps-btn" to="/">
                返回首页
              </Link>
              <Link
                className="ps-btn ghost"
                to={`/success?serial=${encodeURIComponent(result.serial)}&machine=${encodeURIComponent(result.machine)}`}
              >
                查看授权详情
              </Link>
            </div>
          </div>
          <aside className="stack">
            <Callout tone="ok">
              授权已绑定当前机器码，无法转移到其他设备。如需更换设备，请联系客服处理。
            </Callout>
            {config.contact_wechat ? (
              <div className="panel">
                <h2 className="panel-title">客服</h2>
                <p className="muted" style={{ fontSize: 13.5 }}>
                  微信：<span className="mono">{config.contact_wechat}</span>
                </p>
              </div>
            ) : null}
          </aside>
        </div>
      </div>
    )
  }

  return (
    <div className="shell page">
      <div className="page-head">
        <Link to="/" className="back-link">
          ← 返回首页
        </Link>
        <h1 className="page-title">兑换激活码</h1>
        <p className="page-lead">
          输入客服发给你的激活码。激活码与序列号、机器码绑定，兑换后立即生效。
        </p>
      </div>

      <div className="split">
        <div className="panel">
          {error ? (
            <div style={{ marginBottom: 16 }}>
              <Callout tone="danger">{error}</Callout>
            </div>
          ) : null}
          <Form<RedeemValues>
            form={form}
            layout="vertical"
            requiredMark={false}
            initialValues={{
              key: '',
              serial: params.get('serial') ?? '',
              machine: params.get('machine') ?? '',
            }}
            onFinish={handleSubmit}
            disabled={redeem.isPending}
          >
            <Form.Item
              label="激活码"
              name="key"
              rules={[keyValidator]}
              extra="PSP 开头为专业版，PSE 开头为企业版"
            >
              <Input
                size="large"
                placeholder="PSP-XXXX-XXXX-XXXX-XXXX"
                className="mono"
                maxLength={40}
                autoComplete="off"
                spellCheck={false}
              />
            </Form.Item>
            <div className="form-grid">
              <Form.Item label="序列号" name="serial" rules={[serialValidator]}>
                <Input placeholder="PS-XXXX-XXXX-XXXX" className="mono" maxLength={24} />
              </Form.Item>
              <Form.Item
                label="机器码"
                name="machine"
                rules={[machineValidator]}
                extra="客户端「授权」页面可复制"
              >
                <Input placeholder="32 位十六进制" className="mono" maxLength={32} />
              </Form.Item>
            </div>
            <div className="form-actions">
              <Button type="primary" size="large" htmlType="submit" loading={redeem.isPending}>
                兑换激活码
              </Button>
              <Button size="large" onClick={() => form.resetFields()}>
                清空
              </Button>
            </div>
          </Form>
        </div>

        <aside className="stack">
          <SectionCard title="从哪里找到这些信息">
            <ol style={{ paddingLeft: 18, color: 'var(--muted)', fontSize: 13.5, lineHeight: 1.9 }}>
              <li>打开 PyShield 客户端，进入「授权」页面。</li>
              <li>复制页面上的序列号与机器码。</li>
              <li>粘贴到左侧表单，填入激活码后提交。</li>
            </ol>
          </SectionCard>
          <div className="panel">
            <h2 className="panel-title">激活码提示</h2>
            <p className="muted" style={{ fontSize: 13.5 }}>
              激活码区分版本，不能跨版本使用。兑换期限以购买时的约定为准，过期后无法兑换。
            </p>
            <p className="hint" style={{ marginTop: 10 }}>
              专业版以 <span className="mono">PSP-</span> 开头，企业版以{' '}
              <span className="mono">PSE-</span> 开头，共 16 位。
            </p>
          </div>
        </aside>
      </div>
    </div>
  )
}
