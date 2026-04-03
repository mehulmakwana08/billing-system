/* api.js — Cloud-only single-backend API client */

const API = (() => {
  const logger = (typeof window !== 'undefined' && window.AppLogger) ? window.AppLogger : null
  let requestSeq = 0

  function log(level, event, details) {
    if (!logger || typeof logger[level] !== 'function') return
    logger[level](event, details)
  }

  function normalizeBase(url, fallback = '') {
    const trimmed = String(url || '').trim().replace(/\/+$/, '')
    if (!trimmed) return fallback
    return trimmed.endsWith('/api') ? trimmed : `${trimmed}/api`
  }

  const runtimeConfig =
    typeof window !== 'undefined' && window.BILLING_RUNTIME_CONFIG
      ? window.BILLING_RUNTIME_CONFIG
      : {}

  const configuredWebBase = normalizeBase(runtimeConfig.webApiBase || runtimeConfig.apiBase, '')

  const desktopBase = (() => {
    if (typeof window === 'undefined' || !window.electronAPI) return ''
    const origin =
      (typeof window.electronAPI.getBackendOrigin === 'function' && window.electronAPI.getBackendOrigin()) ||
      window.electronAPI.backendOrigin ||
      ''
    return normalizeBase(origin, '')
  })()

  const BASE_URL = (() => {
    if (desktopBase) return desktopBase
    if (configuredWebBase) return configuredWebBase
    if (typeof window !== 'undefined' && window.location && window.location.protocol !== 'file:') {
      return `${window.location.origin}/api`
    }
    return 'http://127.0.0.1:5000/api'
  })()

  let token = localStorage.getItem('auth_token') || ''

  log('info', 'api.client.initialized', {
    base_url: BASE_URL,
    desktop_mode: Boolean(desktopBase),
    has_cached_token: Boolean(token),
  })

  function isOnline() {
    return typeof navigator === 'undefined' ? true : navigator.onLine
  }

  async function parseJson(res) {
    return res.json().catch(() => ({}))
  }

  async function request(method, path, body) {
    const requestId = ++requestSeq
    const startedAt = Date.now()
    const headers = { 'Content-Type': 'application/json' }
    if (token) headers.Authorization = `Bearer ${token}`

    const opts = { method, headers }
    if (body !== undefined) opts.body = JSON.stringify(body)

    log('debug', 'api.request.start', {
      request_id: requestId,
      method,
      path,
      online: isOnline(),
    })

    let res
    try {
      res = await fetch(BASE_URL + path, opts)
    } catch (err) {
      log('error', 'api.request.network_error', {
        request_id: requestId,
        method,
        path,
        duration_ms: Date.now() - startedAt,
        message: err.message,
      })
      throw err
    }

    const durationMs = Date.now() - startedAt
    if (!res.ok) {
      const err = await parseJson(res)
      const message = err.error || err.message || `HTTP ${res.status}`
      log('warn', 'api.request.failed', {
        request_id: requestId,
        method,
        path,
        status: res.status,
        duration_ms: durationMs,
        message,
      })
      throw new Error(message)
    }

    log('debug', 'api.request.success', {
      request_id: requestId,
      method,
      path,
      status: res.status,
      duration_ms: durationMs,
    })

    if (res.status === 204) return {}
    return parseJson(res)
  }

  function setToken(nextToken) {
    const hadToken = Boolean(token)
    token = nextToken || ''
    if (token) localStorage.setItem('auth_token', token)
    else localStorage.removeItem('auth_token')
    log('info', 'api.auth_token.updated', {
      had_token: hadToken,
      has_token: Boolean(token),
    })
  }

  return {
    get: (path) => request('GET', path),
    post: (path, body) => request('POST', path, body),
    put: (path, body) => request('PUT', path, body),
    delete: (path) => request('DELETE', path),

    setToken,
    getToken: () => token,
    clearToken: () => setToken(''),

    getBaseUrl: () => BASE_URL,
    isOnline,
  }
})()
