import { Link } from 'react-router-dom'

import { useSiteConfig } from '../../hooks/useSiteConfig'

const FEATURES = [
  {
    kicker: '01',
    title: '源码加密构建',
    body: '把 Python 模块编译成受保护的字节码运行时，产物可直接分发给最终用户。',
  },
  {
    kicker: '02',
    title: '设备绑定授权',
    body: '授权与机器码一一对应，序列号无法跨设备复用，降低泄露与二次分发风险。',
  },
  {
    kicker: '03',
    title: '离线签名校验',
    body: 'Ed25519 签名授权文档支持离线校验，无需每次启动都联网。',
  },
  {
    kicker: '04',
    title: '构建配额控制',
    body: '按版本设置每月构建次数与席位，试用与商业授权共用同一套配额规则。',
  },
  {
    kicker: '05',
    title: '续期与吊销',
    body: '后台改一次到期日，客户端刷新即可领取新授权；违规授权可即时吊销。',
  },
  {
    kicker: '06',
    title: '升级不换工程',
    body: '从免费版升级到专业版或企业版，只更新授权，不改现有构建配置。',
  },
]

const PLANS = [
  {
    edition: 'trial',
    name: '免费版',
    price: '¥0',
    unit: (days: number) => `/ ${days} 天`,
    note: '适合评估加密效果与集成流程',
    items: (days: number, builds: number) => [
      '完整加密与签名清单',
      '本机绑定，到期自动失效',
      `${builds} 次加密构建配额`,
      `${days} 天完整功能试用`,
    ],
    action: { label: '申请免费试用', to: '/activate', ghost: false },
    featured: false,
    tag: '',
  },
  {
    edition: 'pro',
    name: '专业版',
    price: '按席位',
    unit: () => '/ 年订阅',
    note: '适合独立开发者与小团队交付',
    items: () => [
      '原生运行时（Cython 编译）',
      '产物许可证签发与续期',
      '更高的月度构建配额',
      '多项目交付与团队协作',
    ],
    action: { label: '购买专业版', to: '/upgrade?edition=pro', ghost: false },
    featured: true,
    tag: '推荐',
  },
  {
    edition: 'enterprise',
    name: '企业版',
    price: '私有部署',
    unit: () => '/ 按需报价',
    note: '适合多席位团队与离线交付场景',
    items: () => [
      '离线激活与 CI 构建令牌',
      '撤销列表、审计与配额管理',
      '可对接内部签发服务',
      '私有化部署与定制支持',
    ],
    action: { label: '联系企业版', to: '/upgrade?edition=enterprise', ghost: true },
    featured: false,
    tag: '定制',
  },
]

export default function Home() {
  const { config } = useSiteConfig()
  const days = config.trial_days
  const builds = config.trial_builds

  return (
    <div className="shell">
      <section className="hero">
        <div className="hero-grid">
          <div>
            <span className="eyebrow">
              <span className="dot" />
              Python 加密构建器 · 授权中心
            </span>
            <h1 className="hero-title">
              让每一次交付
              <br />
              都<em>可授权</em>、可追溯
            </h1>
            <p className="hero-lead">
              PyShield 把 Python 源码编译为受保护的运行时，内置设备绑定与离线授权校验。
              申请试用、兑换激活码、续费与吊销，都在这里完成。
            </p>
            <div className="hero-actions">
              <Link className="ps-btn lime lg" to="/activate">
                申请 {days} 天免费试用
              </Link>
              <Link className="ps-btn ghost lg" to="/redeem">
                兑换激活码
              </Link>
            </div>
            <p className="hero-note">无需信用卡 · 序列号绑定本机 · 到期自动失效</p>
          </div>

          <div className="doc-card" aria-hidden="true">
            <span className="doc-flag">Ed25519 已签名</span>
            <div className="doc-head">
              <span className="doc-title">License Document</span>
              <span className="doc-badge">ACTIVE</span>
            </div>
            <dl className="doc-rows">
              <div className="doc-row">
                <dt>serial</dt>
                <dd>PS-4F2A-9C31-7D08</dd>
              </div>
              <div className="doc-row">
                <dt>edition</dt>
                <dd>pro · 专业版</dd>
              </div>
              <div className="doc-row">
                <dt>binding</dt>
                <dd>machine · 3f9c…a71d</dd>
              </div>
              <div className="doc-row">
                <dt>expires</dt>
                <dd>2027-11-15 · 宽限 14 天</dd>
              </div>
              <div className="doc-row">
                <dt>quota</dt>
                <dd>100 builds / month · 3 seats</dd>
              </div>
              <div className="doc-row">
                <dt>revision</dt>
                <dd>7</dd>
              </div>
            </dl>
            <div className="doc-sig">
              <span>sig</span>
              <span className="bar" />
              <span>ed25519</span>
            </div>
          </div>
        </div>
      </section>

      <div className="stat-row">
        <div className="stat">
          <strong>{days} 天</strong>
          <span>完整功能免费试用</span>
        </div>
        <div className="stat">
          <strong>{builds} 次</strong>
          <span>试用期加密构建配额</span>
        </div>
        <div className="stat">
          <strong>本机绑定</strong>
          <span>序列号与机器码一一对应</span>
        </div>
      </div>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">从源码保护到授权交付</h2>
          <p className="section-lead">
            覆盖加密构建、运行时保护、离线授权与续期管理，适合个人工具与商业软件交付。
          </p>
        </div>
        <div className="feature-grid">
          {FEATURES.map((feature) => (
            <article className="feature" key={feature.kicker}>
              <span className="feature-kicker">{feature.kicker}</span>
              <h3>{feature.title}</h3>
              <p>{feature.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="section" id="plans">
        <div className="section-head">
          <h2 className="section-title">选择适合你的版本</h2>
          <p className="section-lead">
            所有版本都包含加密构建与设备绑定。免费试用 {days} 天，最多可完成 {builds} 次构建。
          </p>
        </div>
        <div className="plan-grid">
          {PLANS.map((plan) => (
            <article className={`plan ${plan.featured ? 'featured' : ''}`.trim()} key={plan.edition}>
              {plan.tag ? <span className="plan-tag">{plan.tag}</span> : null}
              <h3>{plan.name}</h3>
              <p className="plan-note">{plan.note}</p>
              <p className="plan-price">
                {plan.price} <small>{plan.unit(days)}</small>
              </p>
              <ul className="plan-list">
                {plan.items(days, builds).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              <Link
                className={`ps-btn block ${plan.action.ghost ? 'ghost' : ''}`.trim()}
                to={plan.action.to}
              >
                {plan.action.label}
              </Link>
            </article>
          ))}
        </div>
      </section>

      <div className="contact-strip">
        <div>
          <strong>不确定选哪个版本？</strong>
          <p>把使用场景告诉客服，我们会给出配额与部署建议。</p>
        </div>
        <div>
          {config.contact_wechat ? (
            <span className="mono">微信 / {config.contact_wechat}</span>
          ) : (
            <Link className="ps-btn ghost" to="/upgrade">
              留下联系方式
            </Link>
          )}
        </div>
      </div>
    </div>
  )
}
