/* settings.js */

let _savingSettings = false

function logSettings(level, event, details = {}) {
  const logger = (typeof window !== 'undefined' && window.AppLogger) ? window.AppLogger : null
  if (!logger || typeof logger[level] !== 'function') return
  logger[level](event, details)
}

async function loadSettings() {
  try {
    logSettings('debug', 'settings.load.start')
    const co = await API.get('/company')
    const form = document.getElementById('settings-form')
    Object.entries(co).forEach(([k, v]) => {
      const el = form.querySelector(`[name="${k}"]`)
      if (el) el.value = v ?? ''
    })
    logSettings('debug', 'settings.load.success', { field_count: Object.keys(co || {}).length })
  } catch (e) {
    logSettings('error', 'settings.load.failed', { message: e.message })
    toast('Could not load settings: ' + e.message, 'error')
  }
}

document.getElementById('settings-form').addEventListener('submit', async (e) => {
  e.preventDefault()
  if (_savingSettings) return

  const form = document.getElementById('settings-form')
  const saveBtn = form.querySelector('button[type="submit"]')
  const data = {}
  new FormData(form).forEach((v, k) => { data[k] = v })

  // Basic GSTIN validation
  if (data.gstin && !/^\d{2}[A-Z]{5}\d{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/.test(data.gstin)) {
    logSettings('warn', 'settings.save.validation_failed', { reason: 'gstin_format' })
    toast('GSTIN format looks incorrect', 'error')
    return
  }

  try {
    logSettings('info', 'settings.save.start', { field_count: Object.keys(data).length })
    _savingSettings = true
    if (saveBtn) saveBtn.disabled = true

    await API.post('/company', data)
    await refreshCompany()   // refresh global cache
    logSettings('info', 'settings.save.success')
    toast('Settings saved successfully!', 'success')
  } catch (err) {
    logSettings('error', 'settings.save.failed', { message: err.message })
    toast('Save failed: ' + err.message, 'error')
  } finally {
    _savingSettings = false
    if (saveBtn) saveBtn.disabled = false
  }
})
