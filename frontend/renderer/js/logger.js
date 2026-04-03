/* logger.js — shared renderer logger */

const AppLogger = (() => {
  const VALID_LEVELS = new Set(['error', 'warn', 'info', 'debug'])
  const SENSITIVE_KEY_PATTERN = /pass(word)?|token|authorization|cookie|secret|jwt/i

  function redactString(value) {
    return String(value)
      .replace(/Bearer\s+[A-Za-z0-9\-._~+/]+=*/gi, 'Bearer [REDACTED]')
      .replace(/(["']?(?:password|token|authorization|cookie|secret)["']?\s*[:=]\s*)[^,\s}]+/gi, '$1[REDACTED]')
  }

  function sanitize(value, depth = 0) {
    if (value === null || value === undefined) return value
    if (depth > 4) return '[TRUNCATED]'

    if (Array.isArray(value)) {
      return value.slice(0, 30).map((entry) => sanitize(entry, depth + 1))
    }

    if (typeof value === 'object') {
      const out = {}
      Object.entries(value).slice(0, 50).forEach(([key, entry]) => {
        if (SENSITIVE_KEY_PATTERN.test(key)) {
          if ((key.startsWith('has_') || key.startsWith('is_')) && typeof entry === 'boolean') {
            out[key] = entry
          } else {
            out[key] = '[REDACTED]'
          }
        } else {
          out[key] = sanitize(entry, depth + 1)
        }
      })
      return out
    }

    if (typeof value === 'string') return redactString(value).slice(0, 2000)
    return value
  }

  function normalizeLevel(level) {
    const candidate = String(level || 'info').trim().toLowerCase()
    const aliases = {
      debug: 'debug',
      info: 'info',
      warn: 'warn',
      warning: 'warn',
      error: 'error',
      err: 'error',
      erroe: 'error',
    }
    const normalized = aliases[candidate] || 'info'
    return VALID_LEVELS.has(normalized) ? normalized : 'info'
  }

  function emit(level, event, details) {
    const normalizedLevel = normalizeLevel(level)
    const payload = {
      at: new Date().toISOString(),
      details: sanitize(details || {}),
    }

    if (window.electronAPI && typeof window.electronAPI.log === 'function') {
      window.electronAPI.log(normalizedLevel, event, payload)
      return
    }

    const writer = typeof console[normalizedLevel] === 'function' ? console[normalizedLevel] : console.log
    writer(`[${event}]`, payload)
  }

  return {
    debug: (event, details) => emit('debug', event, details),
    info: (event, details) => emit('info', event, details),
    warn: (event, details) => emit('warn', event, details),
    error: (event, details) => emit('error', event, details),
  }
})()

if (typeof window !== 'undefined') {
  window.AppLogger = AppLogger
}
