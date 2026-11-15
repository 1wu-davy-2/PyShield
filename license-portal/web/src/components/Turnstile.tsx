import { useEffect, useRef } from 'react'

type TurnstileApi = {
  render: (container: HTMLElement, options: Record<string, unknown>) => string
  remove: (id: string) => void
  reset: (id?: string) => void
}

declare global {
  interface Window {
    turnstile?: TurnstileApi
  }
}

const SCRIPT_ID = 'pyshield-turnstile'
let loader: Promise<void> | null = null

function loadTurnstile(): Promise<void> {
  if (window.turnstile) return Promise.resolve()
  if (loader) return loader
  loader = new Promise<void>((resolve, reject) => {
    const existing = document.getElementById(SCRIPT_ID) as HTMLScriptElement | null
    const script = existing ?? document.createElement('script')
    script.id = SCRIPT_ID
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'
    script.async = true
    script.defer = true
    script.addEventListener('load', () => resolve())
    script.addEventListener('error', () => reject(new Error('turnstile failed to load')))
    if (!existing) document.head.appendChild(script)
  })
  return loader
}

/**
 * Cloudflare Turnstile widget.  Renders nothing when the portal has no site key
 * configured, which is how local development and the test suite run.
 */
export function Turnstile({
  siteKey,
  onToken,
  resetKey = 0,
}: {
  siteKey: string
  onToken: (token: string) => void
  resetKey?: number
}) {
  const container = useRef<HTMLDivElement>(null)
  const callback = useRef(onToken)
  const widgetId = useRef<string | null>(null)
  callback.current = onToken

  useEffect(() => {
    if (!siteKey || !container.current) return
    let disposed = false

    loadTurnstile()
      .then(() => {
        if (disposed || !container.current || !window.turnstile) return
        if (widgetId.current) {
          try {
            window.turnstile.remove(widgetId.current)
          } catch {
            /* widget already gone */
          }
        }
        widgetId.current = window.turnstile.render(container.current, {
          sitekey: siteKey,
          theme: 'light',
          language: 'zh-cn',
          callback: (token: string) => callback.current(token),
          'expired-callback': () => callback.current(''),
          'error-callback': () => callback.current(''),
        })
      })
      .catch(() => callback.current(''))

    return () => {
      disposed = true
      if (widgetId.current && window.turnstile) {
        try {
          window.turnstile.remove(widgetId.current)
        } catch {
          /* widget already gone */
        }
      }
      widgetId.current = null
    }
  }, [siteKey, resetKey])

  if (!siteKey) return null
  return <div className="turnstile-box" ref={container} />
}
