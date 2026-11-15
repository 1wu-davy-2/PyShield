import { Link } from 'react-router-dom'

export default function NotFound() {
  return (
    <div className="shell page">
      <div className="page-head">
        <h1 className="page-title">页面不存在</h1>
        <p className="page-lead">链接可能已经失效，或者地址输入有误。</p>
      </div>
      <div style={{ display: 'flex', gap: 12 }}>
        <Link className="ps-btn" to="/">
          返回首页
        </Link>
        <Link className="ps-btn ghost" to="/redeem">
          兑换激活码
        </Link>
      </div>
    </div>
  )
}
