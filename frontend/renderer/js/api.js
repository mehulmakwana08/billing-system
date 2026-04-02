/* api.js — Cloud-only single-backend API client */

const API = (() => {
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

  function isOnline() {
    return typeof navigator === 'undefined' ? true : navigator.onLine
  }

  async function parseJson(res) {
    return res.json().catch(() => ({}))
  }

  async function request(method, path, body) {
    const headers = { 'Content-Type': 'application/json' }
    if (token) headers.Authorization = `Bearer ${token}`

    const opts = { method, headers }
    if (body !== undefined) opts.body = JSON.stringify(body)

    const res = await fetch(BASE_URL + path, opts)
    if (!res.ok) {
      const err = await parseJson(res)
      throw new Error(err.error || err.message || `HTTP ${res.status}`)
    }

    if (res.status === 204) return {}
    return parseJson(res)
  }

  function setToken(nextToken) {
    token = nextToken || ''
    if (token) localStorage.setItem('auth_token', token)
    else localStorage.removeItem('auth_token')
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
