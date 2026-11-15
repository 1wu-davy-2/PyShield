import { useEffect } from 'react'
import { Link, NavLink, Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { useAdminMe, useLogout } from '../api/hooks'
import { onUnauthorized } from '../api/client'
import { useSiteConfig } from '../hooks/useSiteConfig'
import { Brand, LoadingScreen, ShieldMark } from './ui'

/* -------------------------------------------------------------------------- */
/* public shell                                                               */
/* -------------------------------------------------------------------------- */

export function PublicLayout() {
  const { config } = useSiteConfig()
  const location = useLocation()

  return (
    <div className="site">
      <header className="site-header">
        <div className="shell site-header-inner">
          <Brand name={config.site_name} />
          <nav className="site-nav">
            <a href="/#plans" className={location.hash === '#plans' ? 'active' : ''}>
              版本与价格
            </a>
            <Link to="/redeem" className={location.pathname === '/redeem' ? 'active' : ''}>
              兑换激活码
            </Link>
            <Link to="/admin/login" className="optional">
              管理端
            </Link>
            <Link to="/activate" className="ps-btn">
              申请试用
            </Link>
          </nav>
        </div>
      </header>

      <main className="site-main">
        <Outlet />
      </main>

      <footer className="site-footer">
        <div className="shell site-footer-inner">
          <span>
            © {new Date().getFullYear()} {config.site_name} · 授权与签发服务
          </span>
          <span className="site-footer-links">
            <Link to="/redeem">兑换激活码</Link>
            <Link to="/upgrade">续费与升级</Link>
            <Link to="/admin/login">管理端</Link>
          </span>
        </div>
      </footer>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* admin shell                                                                */
/* -------------------------------------------------------------------------- */

const ADMIN_NAV = [
  { to: '/admin', label: '概览', end: true },
  { to: '/admin/licenses', label: '授权' },
  { to: '/admin/keys', label: '激活码' },
  { to: '/admin/customers', label: '客户' },
  { to: '/admin/orders', label: '订单' },
  { to: '/admin/audit', label: '审计日志' },
  { to: '/admin/signing', label: '签发密钥' },
]

export function AdminLayout() {
  const me = useAdminMe()
  const logout = useLogout()
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    onUnauthorized(() => {
      navigate('/admin/login', { replace: true })
    })
    return () => onUnauthorized(null)
  }, [navigate])

  if (me.isLoading) return <LoadingScreen text="正在校验会话…" />
  if (me.isError || !me.data) {
    return <Navigate to="/admin/login" replace state={{ from: location.pathname }} />
  }

  const handleLogout = () => {
    logout.mutate(undefined, {
      onSettled: () => navigate('/admin/login', { replace: true }),
    })
  }

  return (
    <div className="admin">
      <aside className="admin-side">
        <div className="admin-brand">
          <ShieldMark size={28} />
          <span>
            PyShield
            <small>License Console</small>
          </span>
        </div>

        <div className="admin-nav-group">运营</div>
        <nav className="admin-nav">
          {ADMIN_NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? 'active' : '')}
            >
              <span className="nav-mark" />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="admin-side-foot">
          <div className="who">
            <span>{me.data.admin.username}</span>
            <span style={{ opacity: 0.6 }}>
              {me.data.admin.role === 'admin' ? '管理员' : me.data.admin.role}
            </span>
          </div>
          <button type="button" onClick={handleLogout} disabled={logout.isPending}>
            {logout.isPending ? '正在退出…' : '退出登录'}
          </button>
        </div>
      </aside>

      <main className="admin-main">
        <Outlet context={{ admin: me.data.admin }} />
      </main>
    </div>
  )
}
