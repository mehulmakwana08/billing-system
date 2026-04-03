/* ledger.js — Customer Credit & Debit Ledger */

let _selectedLedgerCustomer = null
let _recordingLedgerPayment = false

function logLedger(level, event, details = {}) {
  const logger = (typeof window !== 'undefined' && window.AppLogger) ? window.AppLogger : null
  if (!logger || typeof logger[level] !== 'function') return
  logger[level](event, details)
}

async function loadLedger(customerId) {
  logLedger('debug', 'ledger.load.start', {
    customer_id: customerId || null,
    selected_customer_id: _selectedLedgerCustomer || null,
  })
  // Populate customer dropdown
  const sel = document.getElementById('ledger-customer')
  if (sel.options.length <= 1) {
    const custs = _customers.length ? _customers : await refreshCustomers()
    custs.forEach(c => {
      const opt = document.createElement('option')
      opt.value = c.id
      opt.textContent = c.name
      sel.appendChild(opt)
    })
  }

  if (customerId) {
    sel.value = customerId
    _selectedLedgerCustomer = customerId
    await fetchLedgerData(customerId)
  } else if (_selectedLedgerCustomer) {
    sel.value = _selectedLedgerCustomer
    await fetchLedgerData(_selectedLedgerCustomer)
  } else {
    clearLedgerView()
  }
  logLedger('debug', 'ledger.load.complete', { selected_customer_id: _selectedLedgerCustomer || null })
}

function clearLedgerView() {
  document.getElementById('ledger-total-credit').textContent = '₹0.00'
  document.getElementById('ledger-total-debit').textContent = '₹0.00'
  document.getElementById('ledger-balance').textContent = '₹0.00'
  document.getElementById('ledger-status').innerHTML = '<span class="badge bg-secondary">—</span>'
  document.querySelector('#ledger-table tbody').innerHTML =
    '<tr><td colspan="6" class="text-center text-muted py-4">Select a customer to view ledger</td></tr>'
}

async function fetchLedgerData(cid) {
  if (!cid) { clearLedgerView(); return }
  try {
    logLedger('debug', 'ledger.fetch.start', { customer_id: Number(cid) })
    const data = await API.get(`/ledger/${cid}`)
    renderLedgerSummary(data)
    renderLedgerTable(data.entries)
    logLedger('debug', 'ledger.fetch.success', {
      customer_id: Number(cid),
      entries: Array.isArray(data.entries) ? data.entries.length : 0,
      balance: data.balance || 0,
    })
  } catch (e) {
    logLedger('error', 'ledger.fetch.failed', { customer_id: Number(cid), message: e.message })
    toast('Failed to load ledger: ' + e.message, 'error')
  }
}

function statusBadgeHTML(status) {
  const map = {
    'Overpaid': 'bg-success',
    'Settled':  'bg-primary',
    'Due':      'bg-danger',
  }
  return `<span class="badge ${map[status] || 'bg-secondary'}">${status}</span>`
}

function renderLedgerSummary(data) {
  document.getElementById('ledger-total-credit').textContent = fmtMoney(data.total_credit)
  document.getElementById('ledger-total-debit').textContent = fmtMoney(data.total_debit)
  const balEl = document.getElementById('ledger-balance')
  balEl.textContent = fmtMoney(Math.abs(data.balance))
  if (data.balance > 0) {
    balEl.textContent = '+' + fmtMoney(data.balance)
    balEl.className = 'stat-value text-success'
  } else if (data.balance < 0) {
    balEl.textContent = '-' + fmtMoney(Math.abs(data.balance))
    balEl.className = 'stat-value text-danger'
  } else {
    balEl.className = 'stat-value'
  }
  document.getElementById('ledger-status').innerHTML = statusBadgeHTML(data.status)
}

function renderLedgerTable(entries) {
  const tbody = document.querySelector('#ledger-table tbody')
  if (!entries || entries.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">No transactions yet</td></tr>'
    return
  }
  tbody.innerHTML = entries.map(e => {
    const isCredit = e.type === 'credit'
    const typeBadge = isCredit
      ? '<span class="badge bg-success">Credit</span>'
      : '<span class="badge bg-danger">Debit</span>'
    const amtClass = isCredit ? 'text-success' : 'text-danger'
    const amtPrefix = isCredit ? '+' : '-'
    const runClass = e.running_balance >= 0 ? 'text-success' : 'text-danger'
    const dateStr = e.created_at ? e.created_at.replace('T', ' ').substring(0, 16) : '—'
    return `
      <tr>
        <td>${dateStr}</td>
        <td>${typeBadge}</td>
        <td class="${amtClass} fw-semibold">${amtPrefix}${fmtMoney(e.amount)}</td>
        <td>${e.description || '—'}</td>
        <td><code>${e.reference_id || '—'}</code></td>
        <td class="${runClass} fw-bold">${fmtMoney(e.running_balance)}</td>
      </tr>
    `
  }).join('')
}

// ── Customer select change ──────────────────────────────────────────────────
document.getElementById('ledger-customer').addEventListener('change', function () {
  _selectedLedgerCustomer = this.value
  if (this.value) {
    fetchLedgerData(this.value)
  } else {
    clearLedgerView()
  }
})

// ── Record Payment ──────────────────────────────────────────────────────────
async function recordLedgerPayment() {
  if (_recordingLedgerPayment) return

  const recordBtn = document.getElementById('ledger-payment-btn')
  const cid = document.getElementById('ledger-customer').value
  if (!cid) { toast('Please select a customer first', 'error'); return }

  const amount = parseFloat(document.getElementById('ledger-pay-amount').value)
  if (!amount || amount <= 0) { toast('Enter a valid payment amount', 'error'); return }

  const desc = document.getElementById('ledger-pay-desc').value.trim() || 'Payment received'
  const mode = document.getElementById('ledger-pay-mode').value
  const ref = document.getElementById('ledger-pay-ref').value.trim()

  try {
    logLedger('info', 'ledger.payment.start', {
      customer_id: Number(cid),
      amount,
      mode,
    })
    _recordingLedgerPayment = true
    if (recordBtn) recordBtn.disabled = true

    await API.post('/ledger/payment', {
      customer_id: parseInt(cid),
      amount,
      description: desc,
      mode,
      reference: ref,
    })
    toast('Payment recorded successfully', 'success')
    // Clear form
    document.getElementById('ledger-pay-amount').value = ''
    document.getElementById('ledger-pay-desc').value = ''
    document.getElementById('ledger-pay-ref').value = ''
    // Refresh ledger
    await fetchLedgerData(cid)
    logLedger('info', 'ledger.payment.success', {
      customer_id: Number(cid),
      amount,
      mode,
    })
  } catch (e) {
    logLedger('error', 'ledger.payment.failed', {
      customer_id: Number(cid),
      amount,
      mode,
      message: e.message,
    })
    toast('Failed to record payment: ' + e.message, 'error')
  } finally {
    _recordingLedgerPayment = false
    if (recordBtn) recordBtn.disabled = false
  }
}

document.getElementById('ledger-payment-form').addEventListener('submit', (e) => {
  e.preventDefault()
  recordLedgerPayment()
})
