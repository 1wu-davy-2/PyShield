import { Alert, App, Button, Checkbox, Form, Input, Segmented } from 'antd'
import { useState } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom'

import { ApiError } from '../../api/client'
import { useApplyTrial } from '../../api/hooks'
import { Turnstile } from '../../components/Turnstile'
import { applyServerErrors } from '../../lib/formErrors'
import { MetaList } from '../../components/ui'
import { useSiteConfig } from '../../hooks/useSiteConfig'
import { shortMachine } from '../../lib/format'

interface TrialFormValues {
  email: string
  phone: string
  name: string
  org_type: 'individual' | 'company'
  company_name?: string
  consent: boolean
}

const phoneValidator = {
  validator: (_rule: unknown, value: string) => {
    const text = String(value ?? '').replace(/[\s\-()（）]/g, '')
    if (!text) return Promise.reject(new Error('请填写手机号'))
    if (text.startsWith('+')) {
      return /^\d{8,15}$/.test(text.slice(1))
        ? Promise.resolve()
        : Promise.reject(new Error('手机号格式不正确'))
    }
    if (!/^\d+$/.test(text)) return Promise.reject(new Error('手机号格式不正确'))
    if (text.length === 11) {
      return /^1[3-9]\d{9}$/.test(text)
        ? Promise.resolve()
        : Promise.reject(new Error('手机号格式不正确'))
    }
    if (text.length >= 11 && text.length <= 15) return Promise.resolve()
    return Promise.reject(new Error('手机号格式不正确'))
  },
}

export default function Activate() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { message } = App.useApp()
  const { config } = useSiteConfig()
  const apply = useApplyTrial()
  const [form] = Form.useForm<TrialFormValues>()
  const orgType = Form.useWatch('org_type', form) ?? 'individual'
  const [captchaToken, setCaptchaToken] = useState('')
  const [captchaNonce, setCaptchaNonce] = useState(0)
  const [formError, setFormError] = useState('')

  const serial = (params.get('serial') ?? '').trim()
  const machine = (params.get('machine') ?? '').trim()
  const edition = (params.get('edition') ?? 'trial').trim().toLowerCase()

  if (edition === 'pro' || edition === 'enterprise') {
    return (
      <Navigate
        to={`/upgrade?edition=${encodeURIComponent(edition)}&serial=${encodeURIComponent(serial)}`}
        replace
      />
    )
  }

  if (!serial || !machine) {
    return (
      <div className="shell page">
        <div className="page-head">
          <h1 className="page-title">请从 PyShield 客户端打开</h1>
          <p className="page-lead">
            试用申请需要携带本机的序列号与机器码，这两个值由客户端自动生成。
          </p>
        </div>
        <div className="split">
          <div className="panel">
            <div className="stack-sm">
              <div className="callout warn">
                当前链接缺少 <span className="mono">serial</span> 或{' '}
                <span className="mono">machine</span> 参数。
              </div>
              <ol style={{ paddingLeft: 18, margin: 0, color: 'var(--muted)', fontSize: 14 }}>
                <li>打开 PyShield 客户端，进入「授权」页面。</li>
                <li>点击「申请试用」，浏览器会自动打开本页。</li>
                <li>如果浏览器没有自动跳转，回到客户端复制链接后重试。</li>
              </ol>
              <div className="form-actions" style={{ borderTop: 'none', paddingTop: 0 }}>
                <Button type="primary" onClick={() => navigate('/')}>
                  返回首页
                </Button>
                <Button onClick={() => navigate('/redeem')}>我已获得激活码</Button>
              </div>
            </div>
          </div>
          <aside className="panel">
            <h2 className="panel-title">试用包含什么</h2>
            <p className="panel-sub">申请通过后立即可用，无需等待人工审核。</p>
            <MetaList
              items={[
                ['试用时长', `${config.trial_days} 天`],
                ['构建配额', `${config.trial_builds} 次`],
                ['设备绑定', '本机机器码'],
                ['版本', '免费版（完整功能）'],
              ]}
            />
          </aside>
        </div>
      </div>
    )
  }

  const handleSubmit = (values: TrialFormValues) => {
    setFormError('')
    if (config.captcha_site_key && !captchaToken) {
      setFormError('请先完成人机验证。')
      return
    }
    apply.mutate(
      {
        serial,
        machine,
        edition: 'trial',
        email: values.email.trim(),
        phone: values.phone.trim(),
        name: values.name.trim(),
        org_type: values.org_type,
        company_name: values.org_type === 'company' ? (values.company_name ?? '').trim() : '',
        consent: Boolean(values.consent),
        captcha_token: captchaToken,
        website: '',
      },
      {
        onSuccess: (result) => {
          if ('verification_required' in result && result.verification_required) {
            navigate(`/verify?application=${result.application_id}`)
            return
          }
          message.success('试用授权已签发')
          navigate(`/success?serial=${encodeURIComponent(serial)}&machine=${encodeURIComponent(machine)}`)
        },
        onError: (error) => {
          if (error instanceof ApiError) {
            applyServerErrors(form, error.errors)
            setFormError(error.message)
          } else {
            setFormError('提交失败，请稍后重试。')
          }
          setCaptchaNonce((value) => value + 1)
          setCaptchaToken('')
        },
      },
    )
  }

  return (
    <div className="shell page">
      <div className="page-head">
        <Link to="/" className="back-link">
          ← 返回首页
        </Link>
        <h1 className="page-title">申请免费试用</h1>
        <p className="page-lead">
          填写联系信息{config.email_verification ? '并验证邮箱' : ''}，即可获得绑定本机的试用授权。
        </p>
      </div>

      <div className="split">
        <div className="panel">
          {formError ? (
            <div style={{ marginBottom: 18 }}>
              <Alert type="error" message={formError} showIcon />
            </div>
          ) : null}

          <Form<TrialFormValues>
            form={form}
            layout="vertical"
            requiredMark={false}
            initialValues={{ org_type: 'individual', consent: false }}
            onFinish={handleSubmit}
            disabled={apply.isPending}
          >
            <div className="form-grid">
              <Form.Item
                label="邮箱"
                name="email"
                rules={[
                  { required: true, message: '请填写邮箱' },
                  { type: 'email', message: '邮箱格式不正确' },
                ]}
                extra="用于接收验证码和授权通知"
              >
                <Input placeholder="you@example.com" autoComplete="email" maxLength={254} />
              </Form.Item>
              <Form.Item
                label="手机号"
                name="phone"
                rules={[phoneValidator]}
                extra="用于身份核对与售后联系"
              >
                <Input placeholder="13800138000" autoComplete="tel" maxLength={20} />
              </Form.Item>
              <Form.Item
                label="姓名或称呼"
                name="name"
                rules={[
                  { required: true, message: '请填写姓名或称呼' },
                  { min: 2, max: 40, message: '长度需在 2–40 个字符之间' },
                ]}
              >
                <Input placeholder="怎么称呼你" autoComplete="name" maxLength={40} />
              </Form.Item>
              <Form.Item label="主体类型" name="org_type" rules={[{ required: true }]}>
                <Segmented
                  block
                  options={[
                    { label: '个人', value: 'individual' },
                    { label: '公司', value: 'company' },
                  ]}
                />
              </Form.Item>
              {orgType === 'company' ? (
                <Form.Item
                  className="span-2"
                  label="公司名称"
                  name="company_name"
                  rules={[
                    { required: true, message: '请填写公司名称' },
                    { min: 2, max: 100, message: '长度需在 2–100 个字符之间' },
                  ]}
                >
                  <Input placeholder="公司或组织全称" autoComplete="organization" maxLength={100} />
                </Form.Item>
              ) : null}
            </div>

            <Form.Item
              name="consent"
              valuePropName="checked"
              rules={[
                {
                  validator: (_rule, value) =>
                    value ? Promise.resolve() : Promise.reject(new Error('请先阅读并同意条款')),
                },
              ]}
            >
              <Checkbox>
                我已阅读并同意隐私政策与服务条款，并同意接收与授权相关的通知邮件。
              </Checkbox>
            </Form.Item>

            <Turnstile
              siteKey={config.captcha_site_key}
              onToken={setCaptchaToken}
              resetKey={captchaNonce}
            />

            <div className="form-actions">
              <Button type="primary" size="large" htmlType="submit" loading={apply.isPending}>
                提交试用申请
              </Button>
              <Button size="large" onClick={() => navigate('/')}>
                取消
              </Button>
            </div>
          </Form>
        </div>

        <aside className="stack">
          <div className="panel">
            <h2 className="panel-title">本次试用</h2>
            <MetaList
              items={[
                ['序列号', <span className="mono">{serial}</span>],
                ['机器码', <span className="mono">{shortMachine(machine)}</span>],
                ['试用时长', `${config.trial_days} 天`],
                ['构建配额', `${config.trial_builds} 次`],
                ['版本', '免费版'],
              ]}
            />
            <p className="hint" style={{ marginTop: 14 }}>
              授权与机器码绑定，无法转移到其他设备。
            </p>
          </div>
          <div className="panel">
            <h2 className="panel-title">信息如何使用</h2>
            <p className="muted" style={{ fontSize: 13.5 }}>
              邮箱用于发送验证码与授权通知，手机号用于身份核对和售后联系。
              我们不会将你的信息用于与授权无关的用途。
            </p>
            {config.contact_wechat ? (
              <p className="hint" style={{ marginTop: 12 }}>
                客服微信：<span className="mono">{config.contact_wechat}</span>
              </p>
            ) : null}
          </div>
        </aside>
      </div>
    </div>
  )
}
