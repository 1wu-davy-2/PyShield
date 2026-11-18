import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, queryString, setCsrfToken } from './client'
import type {
  AuditEvent,
  ClientCheck,
  CreateKeysResult,
  CreatedKey,
  Customer,
  CustomerDetailResult,
  DashboardResult,
  FilterOptions,
  LicenseDetail,
  LicenseKey,
  LicenseSummary,
  ListResult,
  LoginResult,
  MeResult,
  Order,
  OrderStatus,
  PublicConfig,
  PublicLicense,
  SigningResult,
  TrialApplicationMeta,
  TrialApplyResponse,
  TrialFormPayload,
  TrialResendResult,
} from './types'

export const queryKeys = {
  publicConfig: ['public', 'config'] as const,
  publicLicense: (serial: string, machine: string) =>
    ['public', 'license', serial, machine] as const,
  trialApplication: (id: number) => ['public', 'trial-application', id] as const,
  me: ['admin', 'me'] as const,
  dashboard: ['admin', 'dashboard'] as const,
  licenses: (params: Record<string, string>) => ['admin', 'licenses', params] as const,
  license: (serial: string) => ['admin', 'license', serial] as const,
  keys: (params: Record<string, string>) => ['admin', 'keys', params] as const,
  key: (id: number) => ['admin', 'key', id] as const,
  customers: (q: string) => ['admin', 'customers', q] as const,
  customer: (id: number) => ['admin', 'customer', id] as const,
  orders: (status: string) => ['admin', 'orders', status] as const,
  audit: (params: Record<string, string>) => ['admin', 'audit', params] as const,
  checks: (params: Record<string, string>) => ['admin', 'checks', params] as const,
  signing: ['admin', 'signing'] as const,
  filters: ['admin', 'filters'] as const,
}

/* -------------------------------------------------------------------------- */
/* public                                                                     */
/* -------------------------------------------------------------------------- */

export function usePublicConfig() {
  return useQuery({
    queryKey: queryKeys.publicConfig,
    queryFn: () => api.get<PublicConfig>('/api/public/config'),
    staleTime: 10 * 60 * 1000,
    retry: 1,
  })
}

export function usePublicLicense(serial: string, machine: string) {
  return useQuery({
    queryKey: queryKeys.publicLicense(serial, machine),
    queryFn: () =>
      api.get<PublicLicense>(
        `/api/public/license${queryString({ serial, machine })}`,
      ),
    enabled: Boolean(serial && machine),
    retry: false,
  })
}

export function useTrialApplication(id: number) {
  return useQuery({
    queryKey: queryKeys.trialApplication(id),
    queryFn: () =>
      api.get<TrialApplicationMeta>(`/api/public/trial/application${queryString({ id })}`),
    enabled: id > 0,
    retry: false,
  })
}

export function useApplyTrial() {
  return useMutation({
    mutationFn: (payload: TrialFormPayload) =>
      api.post<TrialApplyResponse>('/api/public/trial/apply', payload),
  })
}

export function useVerifyTrial() {
  return useMutation({
    mutationFn: (payload: { application_id: number; code: string }) =>
      api.post<PublicLicense>('/api/public/trial/verify', payload),
  })
}

export function useResendTrialCode() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (applicationId: number) =>
      api.post<TrialResendResult>('/api/public/trial/resend', {
        application_id: applicationId,
      }),
    onSuccess: (_result, applicationId) => {
      client.invalidateQueries({ queryKey: queryKeys.trialApplication(applicationId) })
    },
  })
}

export function useRedeemKey() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (payload: { key: string; serial: string; machine: string }) =>
      api.post<PublicLicense>('/api/public/redeem', payload),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['public', 'license'] })
    },
  })
}

export function useUpgradeRequest() {
  return useMutation({
    mutationFn: (payload: { edition: string; serial?: string; contact: string; notes?: string }) =>
      api.post<{ ok: true; order_id: number; edition: string; contact_wechat: string }>(
        '/api/public/upgrade',
        payload,
      ),
  })
}

/* -------------------------------------------------------------------------- */
/* admin auth                                                                 */
/* -------------------------------------------------------------------------- */

export function useAdminMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: async () => {
      const result = await api.get<MeResult>('/api/admin/me')
      setCsrfToken(result.csrf_token)
      return result
    },
    retry: false,
    staleTime: 5 * 60 * 1000,
  })
}

export function useLogin() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (payload: { username: string; password: string }) =>
      api.post<LoginResult>('/api/admin/login', payload),
    onSuccess: (result) => {
      setCsrfToken(result.csrf_token)
      client.setQueryData(queryKeys.me, {
        ok: true,
        admin: result.admin,
        csrf_token: result.csrf_token,
        expires: result.expires,
      })
    },
  })
}

export function useLogout() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<{ ok: true }>('/api/admin/logout'),
    onSuccess: () => {
      setCsrfToken('')
      client.clear()
    },
  })
}

/* -------------------------------------------------------------------------- */
/* admin data                                                                 */
/* -------------------------------------------------------------------------- */

export function useDashboard() {
  return useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.get<DashboardResult>('/api/admin/dashboard'),
  })
}

export function useFilters() {
  return useQuery({
    queryKey: queryKeys.filters,
    queryFn: () => api.get<FilterOptions>('/api/admin/filters'),
    staleTime: 5 * 60 * 1000,
  })
}

export function useLicenses(params: { q?: string; edition?: string; status?: string }) {
  const normalised = {
    q: params.q ?? '',
    edition: params.edition ?? '',
    status: params.status ?? '',
  }
  return useQuery({
    queryKey: queryKeys.licenses(normalised),
    queryFn: () =>
      api.get<ListResult<LicenseSummary>>(`/api/admin/licenses${queryString(normalised)}`),
    placeholderData: (previous) => previous,
  })
}

export function useLicense(serial: string) {
  return useQuery({
    queryKey: queryKeys.license(serial),
    queryFn: () => api.get<{ ok: true; license: LicenseDetail }>(
      `/api/admin/licenses/${encodeURIComponent(serial)}`,
    ),
    enabled: Boolean(serial),
    retry: false,
  })
}

function useLicenseMutation<TBody>(
  action: 'renew' | 'upgrade' | 'revoke',
) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ serial, body }: { serial: string; body: TBody }) =>
      api.post<{ ok: true; license: LicenseDetail | null; serial: string; action: string }>(
        `/api/admin/licenses/${encodeURIComponent(serial)}/${action}`,
        body,
      ),
    onSuccess: (_result, variables) => {
      client.invalidateQueries({ queryKey: queryKeys.license(variables.serial) })
      client.invalidateQueries({ queryKey: ['admin', 'licenses'] })
      client.invalidateQueries({ queryKey: ['admin', 'customer'] })
      client.invalidateQueries({ queryKey: queryKeys.dashboard })
    },
  })
}

export const useRenewLicense = () =>
  useLicenseMutation<{
    days?: number
    expires?: string
    max_builds?: number | null
    grace_days?: number | null
    notes?: string
  }>('renew')

export const useUpgradeLicense = () =>
  useLicenseMutation<{
    edition: string
    days?: number
    expires?: string
    seats?: number | null
    max_builds?: number | null
    grace_days?: number | null
    notes?: string
  }>('upgrade')

export const useRevokeLicense = () =>
  useLicenseMutation<{ reason: string }>('revoke')

export function useKeys(params: { q?: string; status?: string; edition?: string }) {
  const normalised = {
    q: params.q ?? '',
    status: params.status ?? '',
    edition: params.edition ?? '',
  }
  return useQuery({
    queryKey: queryKeys.keys(normalised),
    queryFn: () => api.get<ListResult<LicenseKey>>(`/api/admin/keys${queryString(normalised)}`),
    placeholderData: (previous) => previous,
  })
}

export function useKey(id: number) {
  return useQuery({
    queryKey: queryKeys.key(id),
    queryFn: () => api.get<{ ok: true; key: LicenseKey }>(`/api/admin/keys/${id}`),
    enabled: id > 0,
    retry: false,
  })
}

export function useCreateKeys() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api.post<CreateKeysResult>('/api/admin/keys', payload),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['admin', 'keys'] })
      client.invalidateQueries({ queryKey: queryKeys.dashboard })
    },
  })
}

export function useRevealKey() {
  return useMutation({
    mutationFn: (id: number) =>
      api.post<{ ok: true; id: number; key: string }>(`/api/admin/keys/${id}/reveal`),
  })
}

export function useRevokeKey() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) =>
      api.post<{ ok: true; id: number; status: string }>(`/api/admin/keys/${id}/revoke`, { reason }),
    onSuccess: (_result, variables) => {
      client.invalidateQueries({ queryKey: queryKeys.key(variables.id) })
      client.invalidateQueries({ queryKey: ['admin', 'keys'] })
      client.invalidateQueries({ queryKey: ['admin', 'customer'] })
      client.invalidateQueries({ queryKey: queryKeys.dashboard })
    },
  })
}

export function useCustomers(search: string) {
  return useQuery({
    queryKey: queryKeys.customers(search),
    queryFn: () => api.get<ListResult<Customer>>(`/api/admin/customers${queryString({ q: search })}`),
    placeholderData: (previous) => previous,
  })
}

export function useCustomer(id: number) {
  return useQuery({
    queryKey: queryKeys.customer(id),
    queryFn: () =>
      api.get<CustomerDetailResult>(`/api/admin/customers/${id}`),
    enabled: id > 0,
    retry: false,
  })
}

export function useOrders(status: string) {
  return useQuery({
    queryKey: queryKeys.orders(status),
    queryFn: () => api.get<ListResult<Order>>(`/api/admin/orders${queryString({ status })}`),
    placeholderData: (previous) => previous,
  })
}

export function useMarkOrder() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      status,
      amount,
    }: {
      id: number
      status: OrderStatus
      amount?: string
    }) => api.post<{ ok: true }>(`/api/admin/orders/${id}/status`, { status, amount }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['admin', 'orders'] })
      client.invalidateQueries({ queryKey: ['admin', 'customer'] })
      client.invalidateQueries({ queryKey: queryKeys.dashboard })
    },
  })
}

export function useAudit(params: { action?: string; q?: string; limit?: number }) {
  const normalised = {
    action: params.action ?? '',
    q: params.q ?? '',
    limit: String(params.limit ?? 200),
  }
  return useQuery({
    queryKey: queryKeys.audit(normalised),
    queryFn: () => api.get<ListResult<AuditEvent>>(`/api/admin/audit${queryString(normalised)}`),
    placeholderData: (previous) => previous,
  })
}

export function useClientChecks(params: {
  serial?: string
  kind?: string
  ok?: string
  limit?: number
}) {
  const normalised = {
    serial: params.serial ?? '',
    kind: params.kind ?? '',
    ok: params.ok ?? '',
    limit: String(params.limit ?? 300),
  }
  return useQuery({
    queryKey: queryKeys.checks(normalised),
    queryFn: () =>
      api.get<ListResult<ClientCheck>>(`/api/admin/client-checks${queryString(normalised)}`),
    placeholderData: (previous) => previous,
  })
}

export function useSetBuildQuota() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      serial,
      limit,
      reset,
    }: {
      serial: string
      limit?: number | null
      reset?: boolean
    }) =>
      api.post<{ ok: true; license: LicenseDetail | null; serial: string; action: string }>(
        `/api/admin/licenses/${encodeURIComponent(serial)}/build-quota`,
        { limit, reset },
      ),
    onSuccess: (_result, variables) => {
      client.invalidateQueries({ queryKey: queryKeys.license(variables.serial) })
      client.invalidateQueries({ queryKey: ['admin', 'licenses'] })
      client.invalidateQueries({ queryKey: ['admin', 'checks'] })
      client.invalidateQueries({ queryKey: queryKeys.dashboard })
    },
  })
}

export function useDeleteLicense() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ serial, confirm }: { serial: string; confirm: string }) =>
      api.post<{ ok: true; serial: string; removed: Record<string, number>; action: string }>(
        `/api/admin/licenses/${encodeURIComponent(serial)}/delete`,
        { confirm },
      ),
    onSuccess: (_result, variables) => {
      client.removeQueries({ queryKey: queryKeys.license(variables.serial) })
      client.invalidateQueries({ queryKey: ['admin', 'licenses'] })
      client.invalidateQueries({ queryKey: ['admin', 'checks'] })
      client.invalidateQueries({ queryKey: ['admin', 'customers'] })
      client.invalidateQueries({ queryKey: ['admin', 'customer'] })
      client.invalidateQueries({ queryKey: ['admin', 'orders'] })
      client.invalidateQueries({ queryKey: ['admin', 'audit'] })
      client.invalidateQueries({ queryKey: queryKeys.dashboard })
    },
  })
}

export function useSigning() {
  return useQuery({
    queryKey: queryKeys.signing,
    queryFn: () => api.get<SigningResult>('/api/admin/signing'),
  })
}

export type { CreatedKey }
