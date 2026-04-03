/* app.js — Router, utilities, global state */

// ── Globals ──────────────────────────────────────────────────────────────────
let _customers = []   // cached customer list
let _products  = []   // cached product list
let _company   = {}   // cached company settings
let _authModal = null
let _isAuthenticated = false
let _authLoginInFlight = false

function setAuthLocked(locked) {
  const layout = document.querySelector('.layout')
  const overlay = document.getElementById('auth-lock-overlay')
  if (layout) layout.classList.toggle('auth-locked', locked)
  if (overlay) {
    overlay.classList.toggle('visible', locked)
    overlay.setAttribute('aria-hidden', locked ? 'false' : 'true')
  }
}

function activateShellPage(name = 'dashboard') {
  document.querySelectorAll('.page').forEach((page) => page.classList.remove('active'))
  document.querySelectorAll('.sidebar-nav a').forEach((link) => link.classList.remove('active'))

  const page = document.getElementById(`page-${name}`)
  if (page) page.classList.add('active')

  const link = document.querySelector(`.sidebar-nav a[data-page="${name}"]`)
  if (link) link.classList.add('active')
}

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

function refreshSyncStatus() {
  if (!_isAuthenticated || !API.getToken()) {
    setSyncStatus('Sign in required', 'warning')
    return
  }
  if (!API.isOnline()) {
    setSyncStatus('Offline', 'warning')
    return
  }
  setSyncStatus('Connected', 'success')
}

function refreshAuthUI(user) {
  const userEl = document.getElementById('auth-user')
  const openBtn = document.getElementById('auth-open-btn')
  const logoutBtn = document.getElementById('auth-logout-btn')
  if (!user) {
    _isAuthenticated = false
    setAuthLocked(true)
    activateShellPage('dashboard')
    userEl.textContent = 'Not signed in'
    openBtn.classList.remove('d-none')
    logoutBtn.classList.add('d-none')
    refreshSyncStatus()
    return
  }
  _isAuthenticated = true
  setAuthLocked(false)
  const displayName = user.username || user.email || 'User'
  userEl.textContent = `${displayName} (Company ${user.company_id})`
  openBtn.classList.add('d-none')
  logoutBtn.classList.remove('d-none')
  refreshSyncStatus()
}

function getAuthModal() {
  if (!_authModal) {
    _authModal = new bootstrap.Modal(document.getElementById('authModal'), {
      backdrop: 'static',
      keyboard: false,
    })
  }
  return _authModal
}

async function syncAuthState() {
  if (!API.getToken()) {
    refreshAuthUI(null)
    return null
  }
  try {
    const me = await API.get('/auth/me')
    refreshAuthUI(me)
    return me
  } catch (_) {
    API.clearToken()
    refreshAuthUI(null)
    return null
  }
}

async function initializeAuthorizedSession() {
  await refreshCompany()
  await Promise.all([refreshCustomers(), refreshProducts()])
  showPage('dashboard')
}

async function authLogin() {
  if (_authLoginInFlight) return

  const username = document.getElementById('login-username').value.trim()
  const password = document.getElementById('login-password').value
  const loginBtn = document.getElementById('login-btn')
  if (!username || !password) {
    toast('Enter username and password', 'error')
    return
  }
  try {
    _authLoginInFlight = true
    if (loginBtn) loginBtn.disabled = true

    const res = await API.post('/auth/login', { username, password })
    API.setToken(res.token)

    // Keep UI locked while initial datasets are fetched to avoid empty New Invoice dropdowns.
    _isAuthenticated = true
    setAuthLocked(true)
    setSyncStatus('Loading data...', 'info')

    if (document.activeElement && typeof document.activeElement.blur === 'function') {
      document.activeElement.blur()
    }
    getAuthModal().hide()

    await initializeAuthorizedSession()
    refreshAuthUI(res.user)
    toast('Signed in', 'success')
  } catch (err) {
    API.clearToken()
    refreshAuthUI(null)
    getAuthModal().show()
    toast('Login failed: ' + err.message, 'error')
  } finally {
    _authLoginInFlight = false
    if (loginBtn) loginBtn.disabled = false
  }
}

function bindAuthControls() {
  document.getElementById('auth-open-btn').addEventListener('click', () => getAuthModal().show())
  document.getElementById('auth-lock-signin-btn')?.addEventListener('click', () => getAuthModal().show())
  document.getElementById('auth-logout-btn').addEventListener('click', () => {
    API.clearToken()
    refreshAuthUI(null)
    getAuthModal().show()
    toast('Signed out', 'info')
  })
  const loginForm = document.getElementById('login-form')

  if (loginForm) {
    loginForm.addEventListener('submit', (event) => {
      event.preventDefault()
      authLogin()
    })
  }
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
  if (!_isAuthenticated || !API.getToken()) {
    setAuthLocked(true)
    document.getElementById('page-title').textContent = 'Sign In Required'
    getAuthModal().show()
    if (name !== 'dashboard') {
      toast('Please sign in first', 'warning')
    }
    return
  }

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
async function openInvoicePDFInBrowser(invoiceId, filename) {
  const token = API.getToken()
  const streamUrl = `${API.getBaseUrl()}/invoices/${invoiceId}/pdf`
  const headers = {}
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(streamUrl, { headers })
  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try {
      const errorPayload = await response.json()
      message = errorPayload.error || errorPayload.message || message
    } catch (_ignored) {
      // Keep the status-based message when response body is not JSON.
    }
    throw new Error(message)
  }

  const blob = await response.blob()
  const blobUrl = URL.createObjectURL(blob)
  const opened = window.open(blobUrl, '_blank')

  if (!opened) {
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = filename || `Invoice_${invoiceId}.pdf`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000)
}

async function openInvoicePDF(invoiceId) {
  try {
    toast('Generating PDF…', 'info')
    const data = await API.get(`/invoices/${invoiceId}/pdf-path`)
    const target = data.path || data.pdf_url

    if (!target) {
      throw new Error('No PDF path returned from server')
    }

    if (String(target).startsWith('http')) {
      const apiRoot = String(API.getBaseUrl() || '').replace(/\/api$/, '')
      const isProtectedApiUrl = apiRoot && String(target).startsWith(`${apiRoot}/api/`)
      if (isProtectedApiUrl) {
        await openInvoicePDFInBrowser(invoiceId, data.filename)
      } else {
        window.open(target, '_blank')
      }
      return
    }

    if (window.electronAPI) {
      const result = await window.electronAPI.openPDF(target)
      if (!result.success) toast('Could not open PDF: ' + result.error, 'error')
    } else {
      await openInvoicePDFInBrowser(invoiceId, data.filename)
    }
  } catch (e) {
    toast('PDF error: ' + e.message, 'error')
  }
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────
let _appBootstrapped = false

async function bootstrapApp() {
  if (_appBootstrapped) return
  _appBootstrapped = true

  bindAuthControls()
  const me = await syncAuthState()
  if (!me) {
    setAuthLocked(true)
    getAuthModal().show()
    return
  }

  await initializeAuthorizedSession()
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    bootstrapApp().catch((err) => {
      console.error('App bootstrap failed:', err)
    })
  }, { once: true })
} else {
  bootstrapApp().catch((err) => {
    console.error('App bootstrap failed:', err)
  })
}
