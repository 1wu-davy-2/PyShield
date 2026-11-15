import { useSigning } from '../../api/hooks'
import { AdminHeader } from '../../components/admin'
import { Callout, CopyButton, DetailItem, ErrorNote, LoadingScreen, SectionCard } from '../../components/ui'
import { formatDateTime } from '../../lib/format'

export default function Signing() {
  const signing = useSigning()

  if (signing.isLoading) return <LoadingScreen text="正在读取签发密钥…" />
  if (signing.isError || !signing.data) {
    return <ErrorNote>{(signing.error as Error)?.message ?? '无法读取签发密钥'}</ErrorNote>
  }

  const { signing_key: key, build_command, prepare_command } = signing.data

  return (
    <>
      <AdminHeader
        title="签发密钥"
        sub="构建器验证授权时使用的 Ed25519 公钥"
      />

      <SectionCard title="密钥信息">
        <div className="detail-grid">
          <DetailItem label="key id" value={key.key_id} mono />
          <DetailItem label="创建时间" value={formatDateTime(key.created)} />
          <DetailItem label="指纹（SHA-256）" value={key.fingerprint} mono />
        </div>
        <div className="divider" />
        <div className="row-between" style={{ marginBottom: 8 }}>
          <div className="detail-label">公钥（Base85）</div>
          <CopyButton value={key.public_key} label="复制公钥" />
        </div>
        <pre className="public-key">{key.public_key}</pre>
      </SectionCard>

      <SectionCard title="构建注入示例">
        <p className="hint" style={{ marginBottom: 12 }}>
          首次构建时把公钥注入客户端；如果构建器已经注入过不同的公钥，需要先 prepare 再构建。
        </p>
        <pre className="public-key" style={{ maxHeight: 'none' }}>
          {prepare_command}
          {'\n'}
          {build_command}
        </pre>
        <div className="form-actions" style={{ borderTop: 'none', paddingTop: 12 }}>
          <CopyButton value={build_command} label="复制构建命令" />
          <CopyButton value={prepare_command} label="复制 prepare 命令" />
        </div>
      </SectionCard>

      <SectionCard title="安全说明">
        <ul style={{ paddingLeft: 18, margin: 0, color: 'var(--muted)', fontSize: 13.5, lineHeight: 2 }}>
          <li>私钥只保存在授权服务器（signing.key，口令加密），绝不随客户端或构建器分发。</li>
          <li>公钥必须在构建商业版时注入客户端；未注入或密钥不匹配的客户端无法验证授权。</li>
          <li>轮换签发密钥后，已发布的旧客户端会验证失败，需要重新构建并下发新的客户端。</li>
          <li>请通过 HTTPS 部署授权中心，并限制管理端访问来源 IP。</li>
        </ul>
        <div style={{ marginTop: 16 }}>
          <Callout tone="warn">
            轮换密钥会让所有旧客户端同时失效。请在低峰期操作，并提前通知客户重新下载。
          </Callout>
        </div>
      </SectionCard>
    </>
  )
}
