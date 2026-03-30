/* app.js — Router, utilities, global state */

// ── Globals ──────────────────────────────────────────────────────────────────
let _customers = []   // cached customer list
let _products  = []   // cached product list
let _company   = {}   // cached company settings
let _authModal = null
let _syncTimer = null
let _syncInFlight = false
let _syncEventsBound = false
let _lastSyncAt = localStorage.getItem('last_sync_at') || ''
let _lastSyncError = ''

function setSyncStatus(text, tone = 'muted') {
  const el = document.getElementById('sync-status')
  if (!el) return
  el.classList.remove('text-muted', 'text-success', 'text-warning', 'text-danger', 'text-info')
  const cls = {
    success: 'text-success',
    warning: 'text-warning',
    danger: 'text-danger',
    info: 'text-info',
    muted: 'text-muted',
  }[tone] || 'text-muted'
  el.classList.add(cls)
  el.textContent = text
}

function formatSyncTime(ts) {
  if (!ts) return ''
  const dt = new Date(ts)
  if (Number.isNaN(dt.getTime())) return ''
  return dt.toLocaleString()
}

function canRunCloudSync() {
  return API.isCloudEnabled() && API.isOnline() && !!API.getToken()
}

function refreshSyncStatus() {
  if (_syncInFlight) {
    setSyncStatus('Syncing...', 'info')
    return
  }
  if (!API.isCloudEnabled()) {
    setSyncStatus('Sync off', 'muted')
    return
  }
  if (!API.isOnline()) {
    setSyncStatus('Offline', 'warning')
    return
  }
  if (!API.getToken()) {
    setSyncStatus('Sign in to sync', 'warning')
    return
  }
  if (_lastSyncError) {
    setSyncStatus('Sync error', 'danger')
    return
  }
  if (_lastSyncAt) {
    setSyncStatus(`Last sync ${formatSyncTime(_lastSyncAt)}`, 'success')
    return
  }
  setSyncStatus('Ready to sync', 'info')
}

function safeParseJson(value) {
  if (typeof value !== 'string') return value || {}
  try {
    return JSON.parse(value)
  } catch (_) {
    return {}
  }
}

async function markLocalQueueItems(queueItems, status, error = '') {
  const message = String(error || '').slice(0, 500)
  for (const item of queueItems) {
    try {
      await API.localPost(`/sync/queue/${item.id}`, { status, error: message })
    } catch (_) {
      // Ignore per-item mark failures; item stays in queue and will be retried.
    }
  }
}

function buildCloudPullChanges(payload) {
  const changes = []
  const invoices = Array.isArray(payload?.invoices) ? payload.invoices : []
  const invoiceItems = Array.isArray(payload?.invoice_items) ? payload.invoice_items : []

  const itemsByInvoice = new Map()
  for (const item of invoiceItems) {
    const list = itemsByInvoice.get(item.invoice_id) || []
    list.push(item)
    itemsByInvoice.set(item.invoice_id, list)
  }

  for (const row of Array.isArray(payload?.customers) ? payload.customers : []) {
    changes.push({ entity: 'customer', action: 'update', payload: row })
  }
  for (const row of Array.isArray(payload?.products) ? payload.products : []) {
    changes.push({ entity: 'product', action: 'update', payload: row })
  }
  for (const row of invoices) {
    changes.push({
      entity: 'invoice',
      action: 'update',
      payload: { ...row, items: itemsByInvoice.get(row.id) || row.items || [] },
    })
  }
  for (const row of Array.isArray(payload?.payments) ? payload.payments : []) {
    changes.push({ entity: 'payment', action: 'update', payload: row })
  }

  const ledgerRows = Array.isArray(payload?.ledger)
    ? payload.ledger
    : Array.isArray(payload?.customer_ledger)
      ? payload.customer_ledger
      : []
  for (const row of ledgerRows) {
    changes.push({ entity: 'ledger', action: 'update', payload: row })
  }

  return changes
}

function refreshActivePageAfterSync() {
  const activePageId = document.querySelector('.page.active')?.id || ''
  switch (activePageId) {
    case 'page-dashboard':
      loadDashboard()
      break
    case 'page-invoices':
      loadInvoices()
      break
    case 'page-customers':
      loadCustomers()
      break
    case 'page-products':
      loadProducts()
      break
    case 'page-ledger':
      loadLedger()
      break
    case 'page-reports':
      initReports()
      break
    case 'page-new-invoice':
      initNewInvoice()
      break
    default:
      break
  }
}

async function runSyncCycle({ force = false } = {}) {
  if (_syncInFlight) return
  if (!canRunCloudSync()) {
    refreshSyncStatus()
    return
  }

  _syncInFlight = true
  _lastSyncError = ''
  refreshSyncStatus()

  try {
    const queueProbeSince = encodeURIComponent(new Date().toISOString())
    const localProbe = await API.localGet(`/sync/pull?since=${queueProbeSince}`)
    const pendingQueue = Array.isArray(localProbe?.pending_queue) ? localProbe.pending_queue : []

    if (pendingQueue.length > 0) {
      const outboundChanges = pendingQueue.map((entry) => ({
        entity: entry.entity,
        action: entry.action,
        payload: safeParseJson(entry.payload),
      }))

      try {
        await API.cloudPost('/sync/push', { changes: outboundChanges })
        await markLocalQueueItems(pendingQueue, 'synced', '')
      } catch (pushErr) {
        await markLocalQueueItems(pendingQueue, 'pending', pushErr.message || 'push_failed')
        throw pushErr
      }
    }

    const pullPath = _lastSyncAt
      ? `/sync/pull?since=${encodeURIComponent(_lastSyncAt)}`
      : '/sync/pull'
    const cloudPayload = await API.cloudGet(pullPath)
    const inboundChanges = buildCloudPullChanges(cloudPayload)

    if (inboundChanges.length > 0) {
      await API.localPost('/sync/push', { changes: inboundChanges })
      await Promise.all([refreshCustomers(), refreshProducts()])
      refreshActivePageAfterSync()
    }

    _lastSyncAt = cloudPayload?.server_time || new Date().toISOString()
    localStorage.setItem('last_sync_at', _lastSyncAt)
    _lastSyncError = ''
  } catch (err) {
    _lastSyncError = err.message || 'sync_failed'
    if (force) {
      console.warn('Sync cycle failed:', _lastSyncError)
    }
  } finally {
    _syncInFlight = false
    refreshSyncStatus()
  }
}

function startSyncLoop() {
  if (_syncTimer) clearInterval(_syncTimer)
  _syncTimer = setInterval(() => {
    runSyncCycle()
  }, 30000)

  if (!_syncEventsBound) {
    window.addEventListener('online', () => runSyncCycle({ force: true }))
    window.addEventListener('offline', refreshSyncStatus)
    _syncEventsBound = true
  }
  refreshSyncStatus()
}

function refreshAuthUI(user) {
  const userEl = document.getElementById('auth-user')
  const openBtn = document.getElementById('auth-open-btn')
  const logoutBtn = document.getElementById('auth-logout-btn')
  if (!user) {
    userEl.textContent = API.isCloudEnabled() ? 'Cloud: not signed in' : 'Offline mode'
    openBtn.classList.remove('d-none')
    logoutBtn.classList.add('d-none')
    refreshSyncStatus()
    return
  }
  userEl.textContent = `${user.email} (Company ${user.company_id})`
  openBtn.classList.add('d-none')
  logoutBtn.classList.remove('d-none')
  refreshSyncStatus()
}

function getAuthModal() {
  if (!_authModal) {
    _authModal = new bootstrap.Modal(document.getElementById('authModal'))
  }
  return _authModal
}

async function syncAuthState() {
  if (!API.getToken()) {
    refreshAuthUI(null)
    return
  }
  try {
    const me = await API.get('/auth/me')
    refreshAuthUI(me)
  } catch (_) {
    API.clearToken()
    refreshAuthUI(null)
  }
}

async function authLogin() {
  const email = document.getElementById('login-email').value.trim()
  const password = document.getElementById('login-password').value
  if (!email || !password) {
    toast('Enter email and password', 'error')
    return
  }
  try {
    const res = await API.post('/auth/login', { email, password })
    API.setToken(res.token)
    refreshAuthUI(res.user)
    getAuthModal().hide()
    runSyncCycle({ force: true })
    toast('Signed in', 'success')
  } catch (err) {
    toast('Login failed: ' + err.message, 'error')
  }
}

async function authRegister() {
  const email = document.getElementById('register-email').value.trim()
  const password = document.getElementById('register-password').value
  const company_id = parseInt(document.getElementById('register-company-id').value || '1', 10)
  if (!email || !password) {
    toast('Enter email and password', 'error')
    return
  }
  try {
    const res = await API.post('/auth/register', { email, password, company_id })
    API.setToken(res.token)
    refreshAuthUI(res.user)
    getAuthModal().hide()
    runSyncCycle({ force: true })
    toast('Account created', 'success')
  } catch (err) {
    toast('Register failed: ' + err.message, 'error')
  }
}

function bindAuthControls() {
  const cloudBase = document.getElementById('cloud-api-base')
  const cloudEnabled = document.getElementById('cloud-enabled')

  cloudBase.value = API.getCloudBase()
  cloudEnabled.checked = API.isCloudEnabled()

  cloudBase.addEventListener('change', () => {
    API.setCloudBase(cloudBase.value)
    toast('Cloud API base updated', 'success')
  })
  cloudEnabled.addEventListener('change', () => {
    API.setCloudEnabled(cloudEnabled.checked)
    syncAuthState()
    refreshSyncStatus()
    if (cloudEnabled.checked) runSyncCycle({ force: true })
  })

  document.getElementById('auth-open-btn').addEventListener('click', () => getAuthModal().show())
  document.getElementById('auth-logout-btn').addEventListener('click', () => {
    API.clearToken()
    refreshAuthUI(null)
    _lastSyncError = ''
    refreshSyncStatus()
    toast('Signed out', 'info')
  })
  document.getElementById('login-btn').addEventListener('click', authLogin)
  document.getElementById('register-btn').addEventListener('click', authRegister)
}

// ── Router ────────────────────────────────────────────────────────────────────
const PAGE_TITLES = {
  'dashboard':    'Dashboard',
  'new-invoice':  'New Invoice',
  'invoices':     'All Invoices',
  'customers':    'Customers',
  'products':     'Products',
  'ledger':       'Customer Ledger',
  'reports':      'Reports & Analytics',
  'settings':     'Settings',
}

function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'))
  document.querySelectorAll('.sidebar-nav a').forEach(a => a.classList.remove('active'))

  const page = document.getElementById(`page-${name}`)
  if (page) page.classList.add('active')

  const link = document.querySelector(`.sidebar-nav a[data-page="${name}"]`)
  if (link) link.classList.add('active')

  document.getElementById('page-title').textContent = PAGE_TITLES[name] || name

  // Lifecycle hooks
  const hooks = {
    'dashboard':    () => loadDashboard(),
    'invoices':     () => loadInvoices(),
    'customers':    () => loadCustomers(),
    'products':     () => loadProducts(),
    'ledger':       () => loadLedger(),
    'reports':      () => initReports(),
    'settings':     () => loadSettings(),
    'new-invoice':  () => initNewInvoice(),
  }
  if (hooks[name]) hooks[name]()
}

// ── Navigation clicks ─────────────────────────────────────────────────────────
document.querySelectorAll('[data-page]').forEach(el => {
  el.addEventListener('click', e => {
    e.preventDefault()
    showPage(el.dataset.page)
  })
})

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type = 'info') {
  const el = document.getElementById('toast')
  el.className = `toast align-items-center border-0 ${type}`
  document.getElementById('toast-msg').textContent = msg
  const t = new bootstrap.Toast(el, { delay: 3000 })
  t.show()
}

// ── Format helpers ────────────────────────────────────────────────────────────
function fmtMoney(n) {
  return '₹' + parseFloat(n || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtDate(d) {
  if (!d) return '—'
  // d is YYYY-MM-DD; display as DD/MM/YYYY
  const parts = d.split('-')
  return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : d
}

function todayISO() {
  return new Date().toISOString().split('T')[0]
}

function statusBadge(status) {
  return status === 'final'
    ? `<span class="badge-final">Final</span>`
    : `<span class="badge-draft">Draft</span>`
}

// ── Indian number to words (client-side, mirrors backend) ────────────────────
const _ones = ['','One','Two','Three','Four','Five','Six','Seven','Eight','Nine',
  'Ten','Eleven','Twelve','Thirteen','Fourteen','Fifteen','Sixteen','Seventeen','Eighteen','Nineteen']
const _tens = ['','','Twenty','Thirty','Forty','Fifty','Sixty','Seventy','Eighty','Ninety']

function belowHundred(n) {
  return n < 20 ? _ones[n] : _tens[Math.floor(n/10)] + (_ones[n%10] ? ' '+_ones[n%10] : '')
}
function belowThousand(n) {
  if (n < 100) return belowHundred(n)
  return _ones[Math.floor(n/100)] + ' Hundred' + (n%100 ? ' '+belowHundred(n%100) : '')
}
function numToWords(amount) {
  amount = Math.round(amount)
  if (amount === 0) return 'Zero Only'
  let n = amount, parts = []
  const crore = Math.floor(n/10000000); n %= 10000000
  const lakh  = Math.floor(n/100000);   n %= 100000
  const thou  = Math.floor(n/1000);     n %= 1000
  if (crore) parts.push(belowThousand(crore)+' Crore')
  if (lakh)  parts.push(belowThousand(lakh) +' Lakh')
  if (thou)  parts.push(belowThousand(thou) +' Thousand')
  if (n)     parts.push(belowThousand(n))
  return parts.join(' ') + ' Only'
}

// ── GST Calculator ────────────────────────────────────────────────────────────
function calcItemGST(qty, rate, gstPct, sellerState, buyerState) {
  const taxable = Math.round(qty * rate * 100) / 100
  const totalTax = Math.round(taxable * gstPct) / 100
  const intraState = (sellerState || '24') === (buyerState || '24')
  const half = Math.round(totalTax / 2 * 100) / 100
  return {
    taxable,
    cgst:  intraState ? half : 0,
    sgst:  intraState ? half : 0,
    igst:  intraState ? 0 : Math.round(totalTax * 100) / 100,
    total: Math.round((taxable + totalTax) * 100) / 100,
  }
}

// ── Caches ─────────────────────────────────────────────────────────────────────
async function refreshCustomers() {
  _customers = await API.get('/customers')
  return _customers
}
async function refreshProducts() {
  _products = await API.get('/products')
  return _products
}
async function refreshCompany() {
  _company = await API.get('/company')
  return _company
}

// ── PDF Open ──────────────────────────────────────────────────────────────────
async function openInvoicePDF(invoiceId) {
  try {
    toast('Generating PDF…', 'info')
    const data = await API.get(`/invoices/${invoiceId}/pdf-path`)
    const target = data.path || data.pdf_url

    if (!target) {
      throw new Error('No PDF path returned from server')
    }

    if (String(target).startsWith('http')) {
      window.open(target, '_blank')
      return
    }

    if (window.electronAPI) {
      const result = await window.electronAPI.openPDF(target)
      if (!result.success) toast('Could not open PDF: ' + result.error, 'error')
    } else {
      // Browser fallback
      const streamUrl = `${API.getBaseUrl()}/invoices/${invoiceId}/pdf`
      window.open(streamUrl, '_blank')
    }
  } catch (e) {
    toast('PDF error: ' + e.message, 'error')
  }
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  bindAuthControls()
  startSyncLoop()
  await syncAuthState()
  await refreshCompany()
  await Promise.all([refreshCustomers(), refreshProducts()])
  runSyncCycle({ force: true })
  showPage('dashboard')
})
