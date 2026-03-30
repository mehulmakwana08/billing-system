/* api.js — Hybrid cloud + offline API client */

const API = (() => {
  const LOCAL_BASE = (() => {
    if (typeof window !== 'undefined' && window.location && window.location.protocol !== 'file:') {
      return `${window.location.origin}/api`
    }
    return 'http://localhost:5000/api'
  })()
  const DEFAULT_CLOUD_BASE = 'https://api.yourdomain.com/api'
  const PLACEHOLDER_HOSTS = new Set([
    'api.yourdomain.com',
    'yourdomain.com',
    'api.example.com',
    'example.com',
  ])

  let cloudBase = normalizeBase(localStorage.getItem('cloud_api_base') || DEFAULT_CLOUD_BASE)
  let cloudEnabled = localStorage.getItem('cloud_enabled') === '1'
  let token = localStorage.getItem('auth_token') || ''

  function normalizeBase(url) {
    const trimmed = String(url || '').replace(/\/+$/, '')
    if (!trimmed) return DEFAULT_CLOUD_BASE
    return trimmed.endsWith('/api') ? trimmed : `${trimmed}/api`
  }

  function isOnline() {
    return typeof navigator === 'undefined' ? true : navigator.onLine
  }

  function isPlaceholderBase(url) {
    try {
      const parsed = new URL(url)
      return PLACEHOLDER_HOSTS.has(parsed.hostname.toLowerCase())
    } catch (_) {
      return true
    }
  }

  function canUseCloud() {
    return cloudEnabled && isOnline() && !isPlaceholderBase(cloudBase)
  }

  function activeBase() {
    return canUseCloud() ? cloudBase : LOCAL_BASE
  }

  async function parseJson(res) {
    return res.json().catch(() => ({}))
  }

  async function call(base, method, path, body) {
    const headers = { 'Content-Type': 'application/json' }
    if (token) headers.Authorization = `Bearer ${token}`

    const opts = { method, headers }
    if (body !== undefined) opts.body = JSON.stringify(body)

    const res = await fetch(base + path, opts)
    if (!res.ok) {
      const err = await parseJson(res)
      throw new Error(err.error || err.message || `HTTP ${res.status}`)
    }

    if (res.status === 204) return {}
    return parseJson(res)
  }

  async function request(method, path, body) {
    const base = activeBase()
    try {
      return await call(base, method, path, body)
    } catch (err) {
      // Auto-fallback to local backend if cloud fails.
      if (base !== LOCAL_BASE) {
        return call(LOCAL_BASE, method, path, body)
      }
      throw err
    }
  }

  async function localRequest(method, path, body) {
    return call(LOCAL_BASE, method, path, body)
  }

  async function cloudRequest(method, path, body) {
    if (isPlaceholderBase(cloudBase)) {
      throw new Error('Cloud API base is not configured')
    }
    return call(cloudBase, method, path, body)
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

    localGet: (path) => localRequest('GET', path),
    localPost: (path, body) => localRequest('POST', path, body),
    localPut: (path, body) => localRequest('PUT', path, body),
    localDelete: (path) => localRequest('DELETE', path),

    cloudGet: (path) => cloudRequest('GET', path),
    cloudPost: (path, body) => cloudRequest('POST', path, body),
    cloudPut: (path, body) => cloudRequest('PUT', path, body),
    cloudDelete: (path) => cloudRequest('DELETE', path),

    // Auth/session utilities for the login UI and desktop sync flow.
    setToken,
    getToken: () => token,
    clearToken: () => setToken(''),

    setCloudBase: (base) => {
      cloudBase = normalizeBase(base)
      localStorage.setItem('cloud_api_base', cloudBase)
      return cloudBase
    },
    getCloudBase: () => cloudBase,

    setCloudEnabled: (enabled) => {
      cloudEnabled = !!enabled
      localStorage.setItem('cloud_enabled', cloudEnabled ? '1' : '0')
      return cloudEnabled
    },
    isCloudEnabled: () => cloudEnabled,

    getBaseUrl: () => activeBase(),
    getLocalBase: () => LOCAL_BASE,
    isOnline,
  }
})()
