/* settings.js */

let _savingSettings = false

async function loadSettings() {
  try {
    const co = await API.get('/company')
    const form = document.getElementById('settings-form')
    Object.entries(co).forEach(([k, v]) => {
      const el = form.querySelector(`[name="${k}"]`)
      if (el) el.value = v ?? ''
    })
  } catch (e) {
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
    toast('GSTIN format looks incorrect', 'error')
    return
  }

  try {
    _savingSettings = true
    if (saveBtn) saveBtn.disabled = true

    await API.post('/company', data)
    await refreshCompany()   // refresh global cache
    toast('Settings saved successfully!', 'success')
  } catch (err) {
    toast('Save failed: ' + err.message, 'error')
  } finally {
    _savingSettings = false
    if (saveBtn) saveBtn.disabled = false
  }
})
