import { Link } from 'react-router-dom'
import { Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import { useDashboard } from '../../api/hooks'
import type { LicenseSummary } from '../../api/types'
import { AdminHeader, Kpi, TABLE_PAGINATION } from '../../components/admin'
import { CopyButton, EditionChip, ErrorNote, LoadingScreen, SectionCard, StatusTag } from '../../components/ui'
import { expiryHint, formatDate } from '../../lib/format'

export default function Dashboard() {
  const dashboard = useDashboard()

  if (dashboard.isLoading) return <LoadingScreen text="正在汇总授权数据…" />
  if (dashboard.isError || !dashboard.data) {
    return <ErrorNote>{(dashboard.error as Error)?.message ?? '无法读取概览数据'}</ErrorNote>
  }

  const { counts, expiring, signing_key } = dashboard.data
  const paid = counts.pro + counts.enterprise

  const columns: ColumnsType<LicenseSummary> = [
    {
      title: '序列号',
      dataIndex: 'serial',
      render: (value: string) => (
        <Link className="mono" to={`/admin/licenses/${encodeURIComponent(value)}`}>
          {value}
        </Link>
      ),
    },
    {
      title: '客户',
      key: 'customer',
      render: (_, row) => (
        <div>
          <div>{row.customer_company || row.customer_name || '—'}</div>
          <div className="hint">{row.customer_email || ''}</div>
        </div>
      ),
    },
    { title: '版本', dataIndex: 'edition', width: 100, render: (value: string) => <EditionChip edition={value} /> },
    {
      title: '到期',
      dataIndex: 'expires',
      width: 150,
      render: (value: string | null) => (
        <div>
          <div>{formatDate(value)}</div>
          <div className="expiry-note urgent">{expiryHint(value)}</div>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'effective_status',
      width: 100,
      render: (value: string) => <StatusTag status={value} />,
    },
  ]

  return (
    <>
      <AdminHeader
        title="概览"
        sub={`共 ${counts.licenses} 条授权记录 · ${counts.customers} 位客户`}
      >
        <Link className="ps-btn ghost" to="/admin/keys/new">
          生成激活码
        </Link>
        <Link className="ps-btn" to="/admin/licenses">
          查看全部授权
        </Link>
      </AdminHeader>

      <div className="kpi-grid">
        <Kpi
          accent
          label="付费授权"
          value={paid}
          hint={`专业版 ${counts.pro} · 企业版 ${counts.enterprise}`}
        />
        <Kpi label="试用授权" value={counts.trials} hint="免费版，到期自动失效" />
        <Kpi label="30 天内到期" value={counts.expiring_30d} hint="可提前联系续费" />
        <Kpi label="客户" value={counts.customers} hint="按邮箱 / 手机号去重" />
        <Kpi label="待兑换激活码" value={counts.keys_unused} hint="已生成未使用" />
        <Kpi label="待处理订单" value={counts.orders_pending} hint="升级 / 续费意向" />
        <Kpi label="已过期" value={counts.expired} hint="含宽限期内未续费" />
        <Kpi label="已吊销" value={counts.revoked} hint="客户端刷新后拒绝" />
      </div>

      <SectionCard
        title="30 天内到期"
        extra={
          expiring.length ? <span className="hint">建议提前 7 天联系客户续费</span> : undefined
        }
        flush
      >
        <Table<LicenseSummary>
          className="ps-table"
          rowKey="serial"
          size="middle"
          columns={columns}
          dataSource={expiring}
          pagination={expiring.length > 20 ? TABLE_PAGINATION : false}
          locale={{ emptyText: '暂无临近到期的授权' }}
        />
      </SectionCard>

      <SectionCard
        title="签发公钥"
        extra={
          <Link className="ps-btn ghost" to="/admin/signing">
            查看完整公钥
          </Link>
        }
      >
        <div className="detail-grid">
          <div className="detail-item">
            <div className="detail-label">key id</div>
            <div className="detail-value mono">{signing_key.key_id}</div>
          </div>
          <div className="detail-item">
            <div className="detail-label">created</div>
            <div className="detail-value">{formatDate(signing_key.created)}</div>
          </div>
          <div className="detail-item">
            <div className="detail-label">fingerprint (sha-256)</div>
            <div className="detail-value mono">{signing_key.fingerprint}</div>
          </div>
        </div>
        <div className="divider" />
        <div className="row-between">
          <p className="hint" style={{ maxWidth: '62ch' }}>
            构建器必须注入同一把公钥才能验证授权；轮换密钥后，已发布的旧客户端需要重新构建。
          </p>
          <CopyButton value={signing_key.public_key} label="复制公钥" />
        </div>
      </SectionCard>
    </>
  )
}
