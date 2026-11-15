import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { Alert, Button, Form, Input } from 'antd'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'

import { useAdminMe, useLogin } from '../../api/hooks'
import { ShieldMark } from '../../components/ui'
import { useSiteConfig } from '../../hooks/useSiteConfig'

interface LoginValues {
  username: string
  password: string
}

export default function Login() {
  const { config } = useSiteConfig()
  const login = useLogin()
  const me = useAdminMe()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from

  if (me.data) return <Navigate to={from && from.startsWith('/admin') ? from : '/admin'} replace />

  const handleSubmit = (values: LoginValues) => {
    login.mutate(values, {
      onSuccess: () => {
        navigate(from && from.startsWith('/admin') ? from : '/admin', { replace: true })
      },
    })
  }

  return (
    <div className="auth">
      <div className="auth-form-side">
        <div className="auth-card">
          <div className="auth-brand">
            <Link to="/" className="brand">
              <ShieldMark />
              <span>
                {config.site_name}
                <span className="brand-sub">License Console</span>
              </span>
            </Link>
          </div>

          <h1>管理员登录</h1>
          <p className="hint" style={{ marginBottom: 26 }}>
            授权、激活码与订单管理仅对管理员开放。
          </p>

          {login.isError ? (
            <div style={{ marginBottom: 16 }}>
              <Alert type="error" showIcon message={(login.error as Error).message} />
            </div>
          ) : null}

          <Form<LoginValues> layout="vertical" requiredMark={false} onFinish={handleSubmit}>
            <Form.Item
              label="用户名"
              name="username"
              rules={[{ required: true, message: '请输入用户名' }]}
            >
              <Input
                size="large"
                prefix={<UserOutlined style={{ color: 'var(--muted-2)' }} />}
                placeholder="admin"
                autoComplete="username"
                maxLength={64}
              />
            </Form.Item>
            <Form.Item
              label="密码"
              name="password"
              rules={[{ required: true, message: '请输入密码' }]}
            >
              <Input.Password
                size="large"
                prefix={<LockOutlined style={{ color: 'var(--muted-2)' }} />}
                placeholder="至少 10 位"
                autoComplete="current-password"
                maxLength={256}
              />
            </Form.Item>
            <Button
              type="primary"
              size="large"
              htmlType="submit"
              block
              loading={login.isPending}
              style={{ marginTop: 8 }}
            >
              登录
            </Button>
          </Form>

          <p className="hint" style={{ marginTop: 22 }}>
            忘记密码？在服务器上执行：
          </p>
          <pre className="login-cmd">python -m license_portal admin reset-password</pre>
          <p className="hint" style={{ marginTop: 10 }}>
            <Link to="/">← 返回公开站点</Link>
          </p>
        </div>
      </div>

      <div className="auth-art">
        <div className="auth-art-inner">
          <div className="rule" />
          <h2>
            授权签发
            <br />
            应当是可审计的
          </h2>
          <p>
            所有写操作都会落入审计日志：谁在什么时间、为哪个序列号、改了哪一项。
            签发私钥只保存在服务器，管理端只能看到公钥与指纹。
          </p>
        </div>
      </div>
    </div>
  )
}
