/**
 * Thin fetch wrapper for the portal JSON API.
 *
 * - Every request is same-origin, so the session cookie travels automatically.
 * - Mutating requests replay the CSRF token that the login endpoint handed out.
 * - Failures become `ApiError` with the server's `code` and per-field `errors`
 *   so forms can highlight the exact input that was rejected.
 */

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly errors: Record<string, string>

  constructor(
    message: string,
    status: number,
    code: string,
    errors: Record<string, string> = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.errors = errors
  }
}

let csrfToken = ''
let unauthorizedHandler: (() => void) | null = null

export function setCsrfToken(token: string): void {
  csrfToken = token
}

export function getCsrfToken(): string {
  return csrfToken
}

export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

export function queryString(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.set(key, String(value))
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}

interface RequestOptions {
  signal?: AbortSignal
}

async function request<T>(
  method: 'GET' | 'POST',
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken

  let response: Response
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: options.signal,
    })
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error
    throw new ApiError('无法连接到授权中心，请检查网络后重试。', 0, 'network_error')
  }

  const text = await response.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = null
    }
  }

  if (!response.ok) {
    const data = (payload ?? {}) as {
      error?: string
      code?: string
      errors?: Record<string, string>
    }
    if (response.status === 401 && unauthorizedHandler) unauthorizedHandler()
    throw new ApiError(
      data.error || `请求失败（HTTP ${response.status}）`,
      response.status,
      data.code || 'error',
      data.errors || {},
    )
  }

  return payload as T
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>('GET', path, undefined, options),
  post: <T>(path: string, body: unknown = {}, options?: RequestOptions) =>
    request<T>('POST', path, body, options),
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return '操作失败，请稍后重试'
}

export function fieldErrors(error: unknown): Record<string, string> {
  return error instanceof ApiError ? error.errors : {}
}
