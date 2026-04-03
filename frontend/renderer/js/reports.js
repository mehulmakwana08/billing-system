/* reports.js */

let _activeReport = 'sales' // 'sales', 'gstr1', 'hsn'
let _salesPeriod  = 'monthly' // 'monthly', 'yearly', 'date', 'all'
let _gstr1Data    = []
let _reportPdfGenerating = false

function logReports(level, event, details = {}) {
  const logger = (typeof window !== 'undefined' && window.AppLogger) ? window.AppLogger : null
  if (!logger || typeof logger[level] !== 'function') return
  logger[level](event, details)
}

function initReports() {
  logReports('debug', 'reports.init.start')
  // Setup customer filter
  const cSel = document.getElementById('report-customer')
  cSel.innerHTML = '<option value="">All Customers</option>'
  _customers.forEach(c => {
    const opt = document.createElement('option')
    opt.value = c.id
    opt.textContent = c.name
    cSel.appendChild(opt)
  })

  // Setup year filter
  const ySel = document.getElementById('report-year')
  ySel.innerHTML = ''
  const curYear = new Date().getFullYear()
  for (let y = curYear - 5; y <= curYear + 2; y++) {
    const opt = document.createElement('option')
    opt.value = y
    opt.textContent = y
    ySel.appendChild(opt)
  }
  ySel.value = curYear

  // Default dates
  const now = new Date()
  const m   = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`
  const d   = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`
  document.getElementById('report-month').value = m
  document.getElementById('report-start-date').value = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-01`
  document.getElementById('report-end-date').value = d

  showReportView('sales')
  loadSalesData()
  logReports('debug', 'reports.init.complete', {
    customer_count: _customers.length,
    active_report: _activeReport,
    sales_period: _salesPeriod,
  })
}

// ── Tab switching ─────────────────────────────────────────────────────────────
function showReportView(type) {
  _activeReport = type
  
  document.getElementById('report-monthly-view').style.display = type === 'sales' ? '' : 'none'
  document.getElementById('report-gstr1-view').style.display   = type === 'gstr1'   ? '' : 'none'
  document.getElementById('report-hsn-view').style.display     = type === 'hsn'     ? '' : 'none'

  // Pickers visibility
  document.getElementById('month-picker-container').style.display = _salesPeriod === 'monthly' ? '' : 'none'
  document.getElementById('year-picker-container').style.display  = _salesPeriod === 'yearly' ? '' : 'none'
  document.getElementById('date-picker-container').style.display  = _salesPeriod === 'date' ? '' : 'none'

  // Button styles
  document.getElementById('salesDropdownBtn').className = type === 'sales' ? 'btn btn-primary dropdown-toggle' : 'btn btn-outline-primary dropdown-toggle'
  document.getElementById('report-gstr1-btn').className = type === 'gstr1' ? 'btn btn-primary' : 'btn btn-outline-primary'
  document.getElementById('report-hsn-btn').className   = type === 'hsn'   ? 'btn btn-primary' : 'btn btn-outline-primary'
}

// Dropdown items logic
document.querySelectorAll('.sales-opt').forEach(opt => {
  opt.addEventListener('click', (e) => {
    e.preventDefault()
    _salesPeriod = e.target.getAttribute('data-val')
    document.getElementById('salesDropdownBtn').textContent = e.target.textContent
    showReportView('sales')
    loadSalesData()
  })
})

document.getElementById('salesDropdownBtn').addEventListener('click', () => {
  if (_activeReport !== 'sales') {
    showReportView('sales')
    loadSalesData()
  }
})

document.getElementById('report-gstr1-btn').addEventListener('click', () => {
  showReportView('gstr1')
  loadGSTR1()
})
document.getElementById('report-hsn-btn').addEventListener('click', () => {
  showReportView('hsn')
  loadHSNSummary()
})

function getReportParams() {
  let params = `?period_type=${_salesPeriod}`
  if (_salesPeriod === 'monthly') {
    params += `&month=${document.getElementById('report-month').value}`
  } else if (_salesPeriod === 'yearly') {
    params += `&year=${document.getElementById('report-year').value}`
  } else if (_salesPeriod === 'date') {
    params += `&start_date=${document.getElementById('report-start-date').value}&end_date=${document.getElementById('report-end-date').value}`
  }
  const cust  = document.getElementById('report-customer').value
  if (cust) params += `&customer_id=${cust}`
  return params
}

// Re-load on picker changes
document.getElementById('report-month').addEventListener('change', reloadCurrentReport)
document.getElementById('report-year').addEventListener('change', reloadCurrentReport)
document.getElementById('report-start-date').addEventListener('change', reloadCurrentReport)
document.getElementById('report-end-date').addEventListener('change', reloadCurrentReport)
document.getElementById('report-customer').addEventListener('change', reloadCurrentReport)

function reloadCurrentReport() {
  if (_activeReport === 'sales') loadSalesData()
  else if (_activeReport === 'gstr1') loadGSTR1()
  else if (_activeReport === 'hsn') loadHSNSummary()
}

// ── Monthly & Yearly Sales ────────────────────────────────────────────────────
async function loadSalesData() {
  const url = `/reports/monthly${getReportParams()}`

  try {
    logReports('debug', 'reports.sales.load.start', { url, period: _salesPeriod })
    const data = await API.get(url)
    const s = data.summary

    document.getElementById('rpt-count').textContent   = s.count
    document.getElementById('rpt-taxable').textContent = fmtMoney(s.taxable)
    document.getElementById('rpt-gst').textContent     = fmtMoney(s.cgst + s.sgst + s.igst)
    document.getElementById('rpt-total').textContent   = fmtMoney(s.grand_total)

    const tbody = document.querySelector('#monthly-table tbody')
    
    if (!data.invoices || data.invoices.length === 0) {
      tbody.innerHTML = `<tr><td colspan="9" class="text-center text-muted py-4">No invoices for this period</td></tr>`
      return
    }
    tbody.innerHTML = data.invoices.map(inv => `
      <tr>
        <td><strong>${inv.invoice_no}</strong></td>
        <td>${fmtDate(inv.date)}</td>
        <td>${inv.customer_name || '—'}</td>
        <td><small>${inv.product_names || '—'}</small></td>
        <td class="text-end">${parseFloat(inv.total_qty || 0).toFixed(0)}</td>
        <td class="text-end">${fmtMoney(inv.taxable_amount)}</td>
        <td class="text-end">${fmtMoney(inv.cgst)}</td>
        <td class="text-end">${fmtMoney(inv.sgst)}</td>
        <td class="text-end fw-semibold text-primary">${fmtMoney(inv.grand_total)}</td>
      </tr>
    `).join('') + `
      <tr class="table-dark fw-bold">
        <td colspan="4">TOTAL</td>
        <td class="text-end">${parseFloat(s.total_qty || 0).toFixed(0)}</td>
        <td class="text-end">${fmtMoney(s.taxable)}</td>
        <td class="text-end">${fmtMoney(s.cgst)}</td>
        <td class="text-end">${fmtMoney(s.sgst)}</td>
        <td class="text-end">${fmtMoney(s.grand_total)}</td>
      </tr>
    `
    logReports('debug', 'reports.sales.load.success', {
      invoice_count: Array.isArray(data.invoices) ? data.invoices.length : 0,
      total_qty: parseFloat(s.total_qty || 0),
      grand_total: s.grand_total || 0,
    })
  } catch (e) {
    logReports('error', 'reports.sales.load.failed', { message: e.message, period: _salesPeriod })
    toast('Report error: ' + e.message, 'error')
  }
}

// ── GSTR-1 ────────────────────────────────────────────────────────────────────
async function loadGSTR1() {
  const url = `/reports/gstr1${getReportParams()}`

  try {
    logReports('debug', 'reports.gstr1.load.start', { url, period: _salesPeriod })
    _gstr1Data = await API.get(url)
    const tbody = document.querySelector('#gstr1-table tbody')

    if (!_gstr1Data || _gstr1Data.length === 0) {
      tbody.innerHTML = `<tr><td colspan="11" class="text-center text-muted py-4">No data for this month</td></tr>`
      return
    }
    tbody.innerHTML = _gstr1Data.map(r => `
      <tr>
        <td>${r.invoice_no}</td>
        <td>${fmtDate(r.date)}</td>
        <td>${r.customer_name || '—'}</td>
        <td><code>${r.customer_gstin || '—'}</code></td>
        <td>${r.product_name || '—'}</td>
        <td>${r.hsn_code || '—'}</td>
        <td class="text-end">${parseFloat(r.qty).toFixed(0)}</td>
        <td class="text-end">${fmtMoney(r.rate)}</td>
        <td class="text-end">${fmtMoney(r.taxable_amount)}</td>
        <td class="text-end">${fmtMoney(r.cgst)}</td>
        <td class="text-end">${fmtMoney(r.sgst)}</td>
      </tr>
    `).join('')
    logReports('debug', 'reports.gstr1.load.success', { count: _gstr1Data.length })
  } catch (e) {
    logReports('error', 'reports.gstr1.load.failed', { message: e.message })
    toast('GSTR-1 error: ' + e.message, 'error')
  }
}

// ── HSN Summary ───────────────────────────────────────────────────────────────
async function loadHSNSummary() {
  const url = `/reports/hsn-summary${getReportParams()}`

  try {
    logReports('debug', 'reports.hsn.load.start', { url, period: _salesPeriod })
    const data = await API.get(url)
    const tbody = document.querySelector('#hsn-table tbody')

    if (!data || data.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted py-4">No data for this month</td></tr>`
      return
    }

    let totTaxable = 0, totCgst = 0, totSgst = 0
    tbody.innerHTML = data.map(r => {
      totTaxable += r.taxable
      totCgst    += r.cgst
      totSgst    += r.sgst
      return `
        <tr>
          <td><code>${r.hsn_code || '—'}</code></td>
          <td>${r.product_name || '—'}</td>
          <td class="text-end">${parseFloat(r.total_qty).toFixed(0)}</td>
          <td class="text-end">${fmtMoney(r.taxable)}</td>
          <td class="text-end">${fmtMoney(r.cgst)}</td>
          <td class="text-end">${fmtMoney(r.sgst)}</td>
          <td class="text-center"><span class="badge bg-info text-dark">${r.gst_percent}%</span></td>
        </tr>
      `
    }).join('') + `
      <tr class="table-dark fw-bold">
        <td colspan="3">TOTAL</td>
        <td class="text-end">${fmtMoney(totTaxable)}</td>
        <td class="text-end">${fmtMoney(totCgst)}</td>
        <td class="text-end">${fmtMoney(totSgst)}</td>
        <td></td>
      </tr>
    `
    logReports('debug', 'reports.hsn.load.success', { count: data.length })
  } catch (e) {
    logReports('error', 'reports.hsn.load.failed', { message: e.message })
    toast('HSN summary error: ' + e.message, 'error')
  }
}

// ── CSV Export ────────────────────────────────────────────────────────────────
document.getElementById('gstr1-export-btn').addEventListener('click', () => {
  if (!_gstr1Data || _gstr1Data.length === 0) {
    logReports('warn', 'reports.gstr1.export.skipped', { reason: 'no_data' })
    toast('No data to export', 'error')
    return
  }
  const headers = ['Invoice No','Date','Customer','GSTIN','Place of Supply',
                   'Product','HSN Code','Qty','Rate','Taxable','CGST','SGST','IGST']
  const rows = _gstr1Data.map(r => [
    r.invoice_no, r.date, r.customer_name, r.customer_gstin || '',
    r.place_of_supply || '', r.product_name, r.hsn_code || '',
    r.qty, r.rate, r.taxable_amount, r.cgst, r.sgst, r.igst
  ])
  const csv = [headers, ...rows]
    .map(row => row.map(v => `"${String(v ?? '').replace(/"/g,'""')}"`).join(','))
    .join('\n')

  const fname = `GSTR1_${_salesPeriod}_Export.csv`

  // Download
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = fname
  a.click()
  URL.revokeObjectURL(url)
  logReports('info', 'reports.gstr1.export.success', { filename: fname, row_count: _gstr1Data.length })
  toast(`Exported ${fname}`, 'success')
})

// ── Consolidated Bill PDF ──────────────────────────────────────────────────
document.getElementById('report-bill-pdf-btn')?.addEventListener('click', async () => {
  if (_reportPdfGenerating) return

  const pdfBtn = document.getElementById('report-bill-pdf-btn')
  const url = `/reports/sales-pdf${getReportParams()}`
  
  try {
    logReports('info', 'reports.sales_pdf.start', { period: _salesPeriod })
    _reportPdfGenerating = true
    if (pdfBtn) pdfBtn.disabled = true

    toast('Generating Report PDF...', 'info')
    const data = await API.get(url)
    const target = data.path || data.pdf_url
    if (!target) {
      throw new Error('No PDF path returned')
    }
    if (String(target).startsWith('http')) {
      window.open(target, '_blank')
      logReports('info', 'reports.sales_pdf.success', { mode: 'url' })
      return
    }
    if (window.electronAPI) {
      const result = await window.electronAPI.openPDF(target)
      if (!result.success) toast('Could not open PDF: ' + result.error, 'error')
      logReports(result.success ? 'info' : 'warn', 'reports.sales_pdf.desktop_result', {
        success: Boolean(result.success),
        message: result.error || '',
      })
    } else {
      // Browser fallback - Since it doesn't stream the PDF directly, just alert
      toast('PDF saved to: ' + target, 'success')
      logReports('info', 'reports.sales_pdf.success', { mode: 'path' })
    }
  } catch (e) {
    logReports('error', 'reports.sales_pdf.failed', { message: e.message })
    toast('Failed to generate PDF: ' + e.message, 'error')
  } finally {
    _reportPdfGenerating = false
    if (pdfBtn) pdfBtn.disabled = false
  }
})


