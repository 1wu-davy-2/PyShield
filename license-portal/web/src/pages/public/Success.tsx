import { CheckCircleFilled } from '@ant-design/icons'
import { App, Button } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { usePublicLicense } from '../../api/hooks'
import { Callout, CopyButton, LoadingScreen, MetaList, StatusTag } from '../../components/ui'
import { editionLabel, formatDate, remainingShort, shortMachine } from '../../lib/format'
import { useSiteConfig } from '../../hooks/useSiteConfig'

export default function Success() {
  const [params] = useSearchParams()
  const { config } = useSiteConfig()
  const { message } = App.useApp()
  const serial = (params.get('serial') ?? '').trim()
  const machine = (params.get('machine') ?? '').trim()
  const license = usePublicLicense(serial, machine)
  const [autoOpened, setAutoOpened] = useState(false)
  const attempted = useRef(false)

  const deepLink = license.data?.deep_link ?? ''
  const isTrial = license.data?.edition === 'trial'
  const active = license.data?.status === 'active'

  useEffect(() => {
    if (attempted.current || !active || !isTrial || !deepLink) return
    attempted.current = true
    const timer = window.setTimeout(() => {
      setAutoOpened(true)
      window.location.href = deepLink
      message.info('已尝试调起 PyShield 客户端')
    }, 1500)
    return () => window.clearTimeout(timer)
  }, [active, deepLink, isTrial, message])

  if (!serial || !machine) {
    return (
      <div className="shell page">
        <div className="page-head">
          <h1 className="page-title">缺少授权信息</h1>
          <p className="page-lead">请从申请成功页面打开，或回到软件内刷新授权状态。</p>
        </div>
        <Link className="ps-btn" to="/">
          返回首页
        </Link>
      </div>
    )
  }

  if (license.isLoading) return <LoadingScreen text="正在读取授权…" />

  if (license.isError) {
    return (
      <div className="shell page">
        <div className="page-head">
          <h1 className="page-title">无法读取授权</h1>
          <p className="page-lead">{(license.error as Error).message}</p>
        </div>
        <Link className="ps-btn" to="/">
          返回首页
        </Link>
      </div>
    )
  }

  if (!active) {
    return (
      <div className="shell page">
        <div className="page-head">
          <h1 className="page-title">授权尚未生效</h1>
          <p className="page-lead">请稍后回到软件内点击「刷新授权状态」。</p>
        </div>
        <div className="panel">
          <StatusTag status={license.data?.status} />
          <p className="muted" style={{ marginTop: 12 }}>
            {license.data?.reason || '授权中心还没有为该设备签发有效授权。'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="shell page">
      <div className="success-hero">
        <div>
          <span className="success-seal">
            <CheckCircleFilled />
          </span>
          <h1 className="page-title" style={{ marginTop: 16 }}>
            {isTrial ? '试用授权已签发' : `${editionLabel(license.data?.edition)}授权已生效`}
          </h1>
          <p className="page-lead">
            授权已绑定当前设备。回到 PyShield 客户端点击「刷新授权状态」即可开始使用。
          </p>

          {isTrial && license.data?.trial_code ? (
            <div style={{ marginTop: 26 }}>
              <div className="hint" style={{ marginBottom: 8 }}>
                试用码（仅用于客户端领取，请勿分享）
              </div>
              <div className="code-display">
                <span>{license.data.trial_code}</span>
                <CopyButton value={license.data.trial_code} label="复制" />
              </div>
            </div>
          ) : null}

          <div className="deep-link-box">
            {isTrial && deepLink ? (
              <>
                <Button
                  type="primary"
                  size="large"
                  href={deepLink}
                  onClick={() => message.info('正在调起 PyShield 客户端…')}
                >
                  在客户端中打开授权
                </Button>
                <CopyButton value={deepLink} label="复制调起链接" />
                <span className="deep-link-note">
                  {autoOpened
                    ? '已尝试自动跳转。没反应就点上面的按钮；仍不行时复制链接在地址栏打开，或回到客户端点「刷新授权状态」。'
                    : '浏览器可能拦截了自动跳转，点击按钮即可调起客户端。'}
                </span>
              </>
            ) : (
              <Callout tone="info">
                已购买授权无需试用码。回到客户端点击「刷新授权状态」即可领取新授权。
              </Callout>
            )}
          </div>

          <div className="next-steps">
            <strong style={{ fontSize: 13.5 }}>接下来</strong>
            <ol>
              <li>回到客户端，点击「刷新授权状态」。</li>
              <li>确认版本与到期日无误后开始加密构建。</li>
              <li>需要更多配额时，可在此页面升级版本。</li>
            </ol>
          </div>
        </div>

        <aside className="panel">
          <h2 className="panel-title">授权摘要</h2>
          <MetaList
            items={[
              ['版本', editionLabel(license.data?.edition)],
              ['序列号', <span className="mono">{license.data?.serial}</span>],
              ['机器码', <span className="mono">{shortMachine(machine)}</span>],
              ['授权 ID', <span className="mono">{license.data?.license_id || '—'}</span>],
              ['到期日', formatDate(license.data?.expires)],
              ['剩余', remainingShort(license.data?.expires)],
              ['每月构建', `${license.data?.max_builds ?? '—'} 次`],
              ['修订号', String(license.data?.revision ?? 0)],
            ]}
          />
          <div className="divider" />
          <div className="stack-sm">
            <Link className="ps-btn ghost block" to={`/upgrade?serial=${encodeURIComponent(serial)}`}>
              查看续费与升级
            </Link>
            <p className="hint" style={{ textAlign: 'center' }}>
              授权与机器码绑定，更换设备请联系客服
            </p>
          </div>
        </aside>
      </div>

      {config.contact_wechat ? (
        <p className="hint" style={{ marginTop: 18, textAlign: 'center' }}>
          遇到问题？联系客服微信 <span className="mono">{config.contact_wechat}</span>
        </p>
      ) : null}
    </div>
  )
}
