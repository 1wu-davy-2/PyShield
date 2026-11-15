import { App, Button, Form, Input } from 'antd'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { ApiError } from '../../api/client'
import { useTrialApplication, useVerifyTrial } from '../../api/hooks'
import { Callout, ErrorNote, LoadingScreen, MetaList, SectionCard } from '../../components/ui'

export default function Verify() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { message } = App.useApp()
  const applicationId = Number(params.get('application') ?? '0')
  const meta = useTrialApplication(applicationId)
  const verify = useVerifyTrial()
  const [form] = Form.useForm<{ code: string }>()

  if (!applicationId) {
    return (
      <div className="shell page">
        <div className="page-head">
          <h1 className="page-title">缺少申请编号</h1>
          <p className="page-lead">请从试用申请页进入，或重新提交申请表。</p>
        </div>
        <Link className="ps-btn" to="/activate">
          重新申请
        </Link>
      </div>
    )
  }

  if (meta.isLoading) return <LoadingScreen text="正在读取申请信息…" />

  const handleSubmit = (values: { code: string }) => {
    verify.mutate(
      { application_id: applicationId, code: values.code.trim() },
      {
        onSuccess: (result) => {
          message.success('邮箱验证通过，试用授权已签发')
          navigate(
            `/success?serial=${encodeURIComponent(result.serial)}&machine=${encodeURIComponent(result.machine)}`,
          )
        },
        onError: (error) => {
          if (error instanceof ApiError && error.errors.code) {
            form.setFields([{ name: 'code', errors: [error.errors.code] }])
          }
        },
      },
    )
  }

  return (
    <div className="shell page">
      <div className="page-head">
        <Link to="/activate" className="back-link">
          ← 返回申请表
        </Link>
        <h1 className="page-title">验证邮箱</h1>
        <p className="page-lead">输入我们发送到邮箱的 6 位验证码，完成试用申请。</p>
      </div>

      <div className="split">
        <div className="panel">
          <Callout tone={meta.isError ? 'danger' : 'ok'}>
            {meta.isError ? (
              <span>{(meta.error as Error).message}</span>
            ) : (
              <span>
                验证码已发送至 <strong className="mono">{meta.data?.email}</strong>
                {meta.data?.status === 'verified' ? '（该申请已完成验证）' : ''}
              </span>
            )}
          </Callout>

          <div style={{ marginTop: 18 }}>
            <Form form={form} layout="vertical" requiredMark={false} onFinish={handleSubmit}>
              <Form.Item
                label="6 位验证码"
                name="code"
                rules={[
                  { required: true, message: '请输入验证码' },
                  { len: 6, message: '验证码为 6 位数字' },
                  { pattern: /^\d{6}$/, message: '验证码为 6 位数字' },
                ]}
                extra="验证码 10 分钟内有效，最多可尝试 5 次。"
              >
                <Input
                  size="large"
                  placeholder="例如 123456"
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  maxLength={6}
                  style={{ letterSpacing: '0.4em', fontFamily: 'var(--font-mono)' }}
                />
              </Form.Item>

              {verify.isError ? <ErrorNote>{(verify.error as Error).message}</ErrorNote> : null}

              <div className="form-actions">
                <Button type="primary" size="large" htmlType="submit" loading={verify.isPending}>
                  验证并开通试用
                </Button>
                <Button size="large" onClick={() => navigate('/activate')}>
                  返回重新填写
                </Button>
              </div>
            </Form>
          </div>
        </div>

        <aside className="stack">
          <SectionCard title="申请信息">
            <MetaList
              items={[
                ['申请编号', <span className="mono">#{applicationId}</span>],
                ['邮箱', <span className="mono">{meta.data?.email ?? '—'}</span>],
                ['序列号', <span className="mono">{meta.data?.serial ?? '—'}</span>],
                ['状态', meta.data?.status ?? '—'],
              ]}
            />
          </SectionCard>
          <div className="panel">
            <h2 className="panel-title">没有收到验证码？</h2>
            <p className="muted" style={{ fontSize: 13.5 }}>
              请先检查垃圾邮件或拦截规则，验证码可能需要一两分钟才能送达。
            </p>
            <p className="hint" style={{ marginTop: 10 }}>
              如果邮箱填写有误，请返回申请表重新提交。重新提交后，旧验证码会立即失效。
            </p>
          </div>
        </aside>
      </div>
    </div>
  )
}
