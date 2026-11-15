export type Edition = 'trial' | 'pro' | 'enterprise'
export type LicenseStatus = 'active' | 'expired' | 'revoked'
export type KeyStatus = 'unused' | 'active' | 'revoked' | 'expired'
export type OrderStatus = 'pending' | 'paid' | 'fulfilled' | 'cancelled'
export type OrgType = 'individual' | 'company'

export interface EditionOption {
  edition: Edition
  name: string
  note: string
}

export interface PublicConfig {
  ok: true
  site_name: string
  support_email: string
  contact_wechat: string
  trial_days: number
  trial_builds: number
  captcha_site_key: string
  email_verification: boolean
  key_redemption_days: number
  editions: EditionOption[]
  version: string
  server_time: string
}

export interface PublicLicense {
  ok: boolean
  status: LicenseStatus | 'unknown' | 'granted' | ''
  serial: string
  machine: string
  license_id: string
  edition: Edition | ''
  expires: string | null
  max_builds: number | null
  revision: number
  trial_code: string
  deep_link: string
  already: boolean
  reason: string
  verification_required: false
}

export interface TrialApplicationResult {
  ok: true
  verification_required: true
  application_id: number
  email: string
  expires_in: number
}

export type TrialApplyResponse = PublicLicense | TrialApplicationResult

export interface TrialApplicationMeta {
  ok: true
  application_id: number
  email: string
  status: string
  edition: Edition
  serial: string
  updated_at: string | null
  code_expires: string | null
}

export interface TrialFormPayload {
  serial: string
  machine: string
  edition: Edition
  email: string
  phone: string
  name: string
  org_type: OrgType
  company_name: string
  consent: boolean
  captcha_token?: string
  website?: string
}

export interface AdminUser {
  username: string
  role: string
}

export interface LoginResult {
  ok: true
  admin: AdminUser
  csrf_token: string
  expires: string
}

export interface MeResult {
  ok: true
  admin: AdminUser
  csrf_token: string
  expires: string
}

export interface SigningKey {
  key_id: string
  public_key: string
  fingerprint: string
  created: string
}

export interface LicenseSummary {
  id?: number
  serial: string
  machine: string
  license_id: string
  edition: Edition
  seats: number
  max_activations: number
  max_builds_per_month: number
  grace_days: number
  issued?: string
  not_before?: string
  expires: string | null
  revision: number
  status?: string
  effective_status: LicenseStatus
  source: string
  display_code?: string
  created_at?: string
  updated_at?: string
  revoked_at?: string | null
  revoked_reason?: string
  features: string[]
  customer_email?: string | null
  customer_phone?: string | null
  customer_name?: string | null
  customer_org_type?: OrgType | null
  customer_company?: string | null
  key_id?: number | null
}

export interface LicenseDetail extends LicenseSummary {
  fingerprints: string[]
  activation_required: boolean
  has_document: boolean
  activations: KeyActivation[]
  events: AuditEvent[]
}

export interface KeyActivation {
  id: number
  key_id: number
  serial: string
  machine: string
  license_id: string
  activated_at: string
}

export interface LicenseKey {
  id: number
  key_prefix: string
  edition: Edition
  days: number
  max_builds_per_month: number
  seats: number
  max_activations: number
  grace_days: number
  features: string[]
  notes: string
  batch_id: number | null
  status: string
  effective_status: KeyStatus
  expires_at: string | null
  created_at: string
  created_by: string
  revoked_at?: string | null
  revoked_reason?: string
  activated_count: number
  activations?: KeyActivation[]
}

export interface CreatedKey {
  id: number
  key: string
  prefix: string
  edition: Edition
  days: number
  max_builds: number
  seats: number
  max_activations: number
  expires_at: string
}

export interface CreateKeysResult {
  ok: true
  batch_id: number | null
  keys: CreatedKey[]
}

export interface DashboardCounts {
  licenses: number
  trials: number
  pro: number
  enterprise: number
  revoked: number
  expired: number
  customers: number
  keys_unused: number
  keys_active: number
  orders_pending: number
  expiring_30d: number
}

export interface DashboardResult {
  ok: true
  counts: DashboardCounts
  expiring: LicenseSummary[]
  signing_key: SigningKey
}

export interface Customer {
  id: number
  email: string
  phone: string
  name: string
  org_type: OrgType
  company_name: string
  consent_at: string
  created_at: string
  updated_at: string
  license_count: number
  last_license_at: string | null
}

export interface Order {
  id: number
  customer_id: number | null
  serial: string
  kind: string
  edition: Edition
  status: OrderStatus
  contact: string
  notes: string
  amount: string
  handled_by: string
  created_at: string
  updated_at: string
  customer_email?: string | null
  customer_phone?: string | null
  customer_name?: string | null
  customer_company?: string | null
}

export interface AuditEvent {
  id: number
  at: string
  actor_type: string
  actor: string
  action: string
  serial: string
  license_id: string
  key_prefix: string
  ip_hash?: string
  detail: Record<string, unknown> | string | null
}

export interface FilterOptions {
  ok: true
  editions: Edition[]
  license_statuses: LicenseStatus[]
  key_statuses: KeyStatus[]
  order_statuses: OrderStatus[]
  audit_actions: string[]
}

export interface SigningResult {
  ok: true
  signing_key: SigningKey
  build_command: string
  prepare_command: string
}

export interface ListResult<T> {
  ok: true
  items: T[]
  total: number
}
