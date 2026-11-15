import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { PublicConfig } from '../api/types'

export const FALLBACK_CONFIG: PublicConfig = {
  ok: true,
  site_name: 'PyShield 授权中心',
  support_email: '',
  contact_wechat: '',
  trial_days: 14,
  trial_builds: 3,
  captcha_site_key: '',
  email_verification: true,
  key_redemption_days: 30,
  editions: [
    { edition: 'trial', name: '免费版', note: '评估加密效果与集成流程' },
    { edition: 'pro', name: '专业版', note: '独立开发者与小团队交付' },
    { edition: 'enterprise', name: '企业版', note: '多席位团队与离线交付' },
  ],
  version: '',
  server_time: '',
}

export function useSiteConfig() {
  const query = useQuery({
    queryKey: ['public', 'config'],
    queryFn: () => api.get<PublicConfig>('/api/public/config'),
    staleTime: 10 * 60 * 1000,
    retry: 1,
  })
  return { config: query.data ?? FALLBACK_CONFIG, loading: query.isLoading }
}
