/* app.js — Router, utilities, global state */

// ── Globals ──────────────────────────────────────────────────────────────────
let _customers = []   // cached customer list
let _products  = []   // cached product list
let _company   = {}   // cached company settings

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
    if (window.electronAPI) {
      const result = await window.electronAPI.openPDF(data.path)
      if (!result.success) toast('Could not open PDF: ' + result.error, 'error')
    } else {
      // Browser fallback
      window.open(`http://localhost:5000/api/invoices/${invoiceId}/pdf`, '_blank')
    }
  } catch (e) {
    toast('PDF error: ' + e.message, 'error')
  }
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await refreshCompany()
  await Promise.all([refreshCustomers(), refreshProducts()])
  showPage('dashboard')
})
