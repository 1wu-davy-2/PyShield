import { App, Button, Form, Input, Segmented } from 'antd'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError } from '../../api/client'
import { useUpgradeRequest } from '../../api/hooks'
import { Callout, MetaList } from '../../components/ui'
import { applyServerErrors } from '../../lib/formErrors'
import { useSiteConfig } from '../../hooks/useSiteConfig'

interface UpgradeValues {
  edition: 'pro' | 'enterprise'
  serial?: string
  contact: string
  notes?: string
}

const PLAN_POINTS: Record<string, string[]> = {
  pro: ['按席位年订阅', '原生运行时与产物签发', '更高的月度构建配额', '适合 1–10 人团队'],
  enterprise: ['私有化部署与离线激活', '撤销列表与审计', '对接内部签发服务', '按需报价，含实施支持'],
}

export default function Upgrade() {
  const [params] = useSearchParams()
  const { config } = useSiteConfig()
  const { message } = App.useApp()
  const request = useUpgradeRequest()
  const [form] = Form.useForm<UpgradeValues>()
  const initialEdition = params.get('edition') === 'enterprise' ? 'enterprise' : 'pro'
  const [edition, setEdition] = useState<'pro' | 'enterprise'>(initialEdition)
  const [order, setOrder] = useState<{ order_id: number; edition: string; contact_wechat: string } | null>(
    null,
  )
  const [error, setError] = useState('')

  useEffect(() => {
    form.setFieldValue('edition', edition)
  }, [edition, form])

  const handleSubmit = (values: UpgradeValues) => {
    setError('')
    request.mutate(
      {
        edition: values.edition,
        serial: (values.serial ?? '').trim() || undefined,
        contact: values.contact.trim(),
        notes: (values.notes ?? '').trim(),
      },
      {
        onSuccess: (result) => {
          setOrder(result)
          message.success('已收到你的需求')
        },
        onError: (err) => {
          if (err instanceof ApiError) {
            applyServerErrors(form, err.errors)
            setError(err.message)
          } else {
            setError('提交失败，请稍后重试。')
          }
        },
      },
    )
  }

  if (order) {
    return (
      <div className="shell page">
        <div className="page-head">
          <h1 className="page-title">已收到你的需求</h1>
          <p className="page-lead">
            订单编号 <span className="mono">#{order.order_id}</span>，我们会在一个工作日内联系你。
          </p>
        </div>
        <div className="split">
          <div className="panel">
            <Callout tone="ok">
              请添加客服微信并备注订单号，方便我们快速匹配你的需求与报价。
            </Callout>
            <div style={{ marginTop: 18 }}>
              <MetaList
                items={[
                  ['订单编号', <span className="mono">#{order.order_id}</span>],
                  ['目标版本', order.edition === 'enterprise' ? '企业版' : '专业版'],
                  [
                    '客服微信',
                    <span className="mono">{order.contact_wechat || config.contact_wechat || '见邮件通知'}</span>,
                  ],
                ]}
              />
            </div>
            <div className="form-actions">
              <Link className="ps-btn" to="/">
                返回首页
              </Link>
              <Link className="ps-btn ghost" to="/redeem">
                我已获得激活码
              </Link>
            </div>
          </div>
          <aside className="panel">
            <h2 className="panel-title">接下来会发生什么</h2>
            <ol style={{ paddingLeft: 18, color: 'var(--muted)', fontSize: 13.5, lineHeight: 1.9 }}>
              <li>客服确认版本、席位与部署方式。</li>
              <li>收款后由管理员生成激活码并发送给你。</li>
              <li>在「兑换激活码」页面输入序列号与激活码即可生效。</li>
            </ol>
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
        <h1 className="page-title">续费与升级</h1>
        <p className="page-lead">
          留个联系方式，我们会根据你的席位与构建量给出方案。已有授权可直接填写序列号。
        </p>
      </div>

      <div className="split">
        <div className="panel">
          {error ? (
            <div style={{ marginBottom: 16 }}>
              <Callout tone="danger">{error}</Callout>
            </div>
          ) : null}
          <Form<UpgradeValues>
            form={form}
            layout="vertical"
            requiredMark={false}
            initialValues={{ edition, serial: params.get('serial') ?? '' }}
            onFinish={handleSubmit}
            disabled={request.isPending}
          >
            <Form.Item label="目标版本" name="edition" rules={[{ required: true }]}>
              <Segmented
                block
                options={[
                  { label: '专业版', value: 'pro' },
                  { label: '企业版', value: 'enterprise' },
                ]}
                onChange={(value) => setEdition(value as 'pro' | 'enterprise')}
              />
            </Form.Item>
            <Form.Item
              label="现有序列号"
              name="serial"
              extra="可选。填写后可自动关联已有授权与客户信息。"
            >
              <Input placeholder="PS-XXXX-XXXX-XXXX" className="mono" maxLength={32} />
            </Form.Item>
            <Form.Item
              label="联系方式"
              name="contact"
              rules={[
                { required: true, message: '请留一个联系方式' },
                { min: 2, max: 120, message: '长度需在 2–120 个字符之间' },
              ]}
              extra="微信号 / 手机号 / 邮箱，任选其一"
            >
              <Input placeholder="微信号 / 手机号 / 邮箱" maxLength={120} />
            </Form.Item>
            <Form.Item label="备注" name="notes">
              <Input.TextArea
                rows={4}
                maxLength={1000}
                showCount
                placeholder="席位数量、部署方式、预期时间等"
              />
            </Form.Item>
            <div className="form-actions">
              <Button type="primary" size="large" htmlType="submit" loading={request.isPending}>
                提交需求
              </Button>
              <Link className="ps-btn ghost lg" to="/">
                返回首页
              </Link>
            </div>
          </Form>
        </div>

        <aside className="stack">
          <div className="panel">
            <h2 className="panel-title">{edition === 'pro' ? '专业版' : '企业版'}包含</h2>
            <ul className="plan-list" style={{ marginTop: 8, borderTop: 'none', paddingTop: 0 }}>
              {PLAN_POINTS[edition].map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
          <div className="panel">
            <h2 className="panel-title">已有激活码？</h2>
            <p className="muted" style={{ fontSize: 13.5 }}>
              如果已经拿到激活码，不需要在这里下单，直接去兑换页面输入即可。
            </p>
            <Link className="ps-btn ghost block" to="/redeem" style={{ marginTop: 14 }}>
              前往兑换激活码
            </Link>
          </div>
        </aside>
      </div>
    </div>
  )
}
