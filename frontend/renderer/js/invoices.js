/* invoices.js — New invoice creation and invoice list */

let _itemRowId = 0
let _viewInvoiceId = null
let _sellerState = '24'

// ════════════════════════════════════════════════════════════════
//  NEW INVOICE
// ════════════════════════════════════════════════════════════════

async function initNewInvoice() {
  // Set defaults
  document.getElementById('inv-date').value = todayISO()
  document.getElementById('inv-notes').value = ''

  // Load next invoice number
  try {
    const data = await API.get('/invoices/next-number')
    document.getElementById('inv-no').value = data.invoice_no
  } catch (e) { /* ignore */ }

  // Populate customer dropdown
  const sel = document.getElementById('customer-select')
  sel.innerHTML = '<option value="">— Select Customer —</option>'
  _customers.forEach(c => {
    const opt = document.createElement('option')
    opt.value = c.id
    opt.textContent = c.name
    opt.dataset.address = c.address || ''
    opt.dataset.gstin = c.gstin || ''
    opt.dataset.state = c.state_code || '24'
    sel.appendChild(opt)
  })

  // Clear items
  document.getElementById('items-body').innerHTML = ''
  _itemRowId = 0
  updateTotals()

  // Add one blank row
  addItemRow()

  // Get seller state
  _sellerState = (_company && _company.state_code) || '24'
}

// ── Customer select handler ───────────────────────────────────────────────────
document.getElementById('customer-select').addEventListener('change', function () {
  const opt = this.options[this.selectedIndex]
  document.getElementById('cust-address').value = opt.dataset.address || ''
  document.getElementById('cust-gstin').value   = opt.dataset.gstin  || ''
  document.getElementById('cust-state').value   = opt.dataset.state  || ''
  updateTotals()
})

// ── Add item row ──────────────────────────────────────────────────────────────
function addItemRow(item = {}) {
  const id = ++_itemRowId
  const tbody = document.getElementById('items-body')

  // Build product options
  const prodOpts = _products.map(p =>
    `<option value="${p.id}" data-hsn="${p.hsn_code||''}" data-rate="${p.default_rate||0}" data-gst="${p.gst_percent||18}">${p.name}</option>`
  ).join('')

  const row = document.createElement('tr')
  row.id = `item-row-${id}`
  row.innerHTML = `
    <td>
      <select class="form-select item-product" data-row="${id}" style="width:100%">
        <option value="">— Select Product —</option>
        ${prodOpts}
      </select>
    </td>
    <td><input type="text" class="item-hsn" data-row="${id}" style="width:80px" value="${item.hsn_code||''}"/></td>
    <td><input type="number" class="item-qty" data-row="${id}" style="width:70px" value="${item.qty||1}" min="0.01" step="any"/></td>
    <td><input type="number" class="item-rate" data-row="${id}" style="width:80px" value="${item.rate||0}" min="0" step="any"/></td>
    <td>
      <select class="item-gst" data-row="${id}" style="width:65px">
        ${[0,5,12,18,28].map(g => `<option value="${g}" ${parseFloat(item.gst_percent||18)===g?'selected':''}>${g}%</option>`).join('')}
      </select>
    </td>
    <td class="text-end item-taxable" id="taxable-${id}">0.00</td>
    <td class="text-end item-cgst-val" id="cgst-${id}">0.00</td>
    <td class="text-end item-sgst-val" id="sgst-${id}">0.00</td>
    <td class="text-end fw-semibold item-total-val" id="total-${id}">0.00</td>
    <td class="text-center"><button type="button" class="btn-del-row" onclick="removeItemRow(${id})"><i class="fas fa-times"></i></button></td>
  `
  tbody.appendChild(row)

  // If item has product_id, set the select
  if (item.product_id) {
    const ps = row.querySelector('.item-product')
    ps.value = item.product_id
  }

  // Wire up change events
  row.querySelectorAll('.item-product,.item-qty,.item-rate,.item-gst').forEach(el => {
    el.addEventListener('change', () => recalcRow(id))
    el.addEventListener('input',  () => recalcRow(id))
  })
  row.querySelector('.item-product').addEventListener('change', function () {
    const opt = this.options[this.selectedIndex]
    if (opt.value) {
      row.querySelector('.item-hsn').value  = opt.dataset.hsn  || ''
      row.querySelector('.item-rate').value = opt.dataset.rate || ''
      row.querySelector('.item-gst').value  = opt.dataset.gst  || '18'
      recalcRow(id)
    }
  })

  recalcRow(id)
}

document.getElementById('add-item-btn').addEventListener('click', () => addItemRow())

function removeItemRow(id) {
  const row = document.getElementById(`item-row-${id}`)
  if (row) row.remove()
  updateTotals()
}

// ── Recalc one row ────────────────────────────────────────────────────────────
function recalcRow(id) {
  const row = document.getElementById(`item-row-${id}`)
  if (!row) return

  const qty    = parseFloat(row.querySelector('.item-qty').value)  || 0
  const rate   = parseFloat(row.querySelector('.item-rate').value) || 0
  const gstPct = parseFloat(row.querySelector('.item-gst').value)  || 0
  const buyer  = document.getElementById('cust-state').value || '24'

  const g = calcItemGST(qty, rate, gstPct, _sellerState, buyer)

  document.getElementById(`taxable-${id}`).textContent = g.taxable.toFixed(2)
  document.getElementById(`cgst-${id}`).textContent    = g.cgst.toFixed(2)
  document.getElementById(`sgst-${id}`).textContent    = g.sgst.toFixed(2)
  document.getElementById(`total-${id}`).textContent   = g.total.toFixed(2)

  updateTotals()
}

// ── Update footer totals ──────────────────────────────────────────────────────
function updateTotals() {
  let taxable = 0, cgst = 0, sgst = 0, igst = 0
  const buyer = document.getElementById('cust-state').value || '24'

  document.querySelectorAll('#items-body tr').forEach(row => {
    taxable += parseFloat(row.querySelector('.item-taxable')?.textContent || 0)
    cgst    += parseFloat(row.querySelector('.item-cgst-val')?.textContent || 0)
    sgst    += parseFloat(row.querySelector('.item-sgst-val')?.textContent || 0)
  })

  // If inter-state, move to IGST
  if (_sellerState !== buyer) {
    igst = Math.round((cgst + sgst) * 100) / 100
    cgst = 0; sgst = 0
  }

  const grand = Math.round((taxable + cgst + sgst + igst) * 100) / 100

  document.getElementById('tot-taxable').textContent = taxable.toFixed(2)
  document.getElementById('tot-cgst').textContent    = cgst.toFixed(2)
  document.getElementById('tot-sgst').textContent    = sgst.toFixed(2)
  document.getElementById('tot-igst').textContent    = igst.toFixed(2)
  document.getElementById('tot-grand').textContent   = grand.toFixed(2)
  document.getElementById('amount-words-display').textContent = numToWords(grand)
}

// ── Collect items from rows ───────────────────────────────────────────────────
function collectItems() {
  const items = []
  const buyer = document.getElementById('cust-state').value || '24'

  document.querySelectorAll('#items-body tr').forEach(row => {
    const prodSel = row.querySelector('.item-product')
    const pid     = prodSel?.value || null
    const pname   = prodSel?.options[prodSel.selectedIndex]?.text || ''
    const hsn     = row.querySelector('.item-hsn')?.value  || ''
    const qty     = parseFloat(row.querySelector('.item-qty')?.value)  || 0
    const rate    = parseFloat(row.querySelector('.item-rate')?.value) || 0
    const gstPct  = parseFloat(row.querySelector('.item-gst')?.value)  || 0

    if (qty <= 0 || rate <= 0) return

    const g = calcItemGST(qty, rate, gstPct, _sellerState, buyer)
    items.push({
      product_id: pid, product_name: pname, hsn_code: hsn,
      qty, rate, gst_percent: gstPct,
      taxable_amount: g.taxable, cgst: g.cgst, sgst: g.sgst, igst: g.igst,
    })
  })
  return items
}

// ── Form submit ───────────────────────────────────────────────────────────────
document.getElementById('invoice-form').addEventListener('submit', async (e) => {
  e.preventDefault()
  const custSel = document.getElementById('customer-select')
  if (!custSel.value) { toast('Please select a customer', 'error'); return }

  const items = collectItems()
  if (items.length === 0) { toast('Add at least one line item', 'error'); return }

  const custOpt = custSel.options[custSel.selectedIndex]
  const buyer   = document.getElementById('cust-state').value || '24'

  const payload = {
    invoice_no:           document.getElementById('inv-no').value.trim(),
    invoice_type:         document.getElementById('inv-type').value,
    date:                 document.getElementById('inv-date').value,
    customer_id:          parseInt(custSel.value),
    customer_name:        custOpt.text,
    customer_address:     document.getElementById('cust-address').value,
    customer_gstin:       document.getElementById('cust-gstin').value,
    customer_state_code:  buyer,
    customer_state_name:  buyer === '24' ? 'Gujarat' : '',
    notes:                document.getElementById('inv-notes').value,
    status:               'final',
    items,
  }

  try {
    const created = await API.post('/invoices', payload)
    toast(`Invoice ${created.invoice_no} saved!`, 'success')

    // Offer to open PDF
    setTimeout(async () => {
      const open = window.electronAPI
        ? await window.electronAPI.confirm(`Invoice saved! Open PDF for ${created.invoice_no}?`)
        : confirm(`Invoice saved! Open PDF for ${created.invoice_no}?`)
      if (open) await openInvoicePDF(created.id)
    }, 400)

    // Reset form for next invoice
    await refreshCustomers()
    initNewInvoice()
  } catch (err) {
    toast('Save failed: ' + err.message, 'error')
  }
})

document.getElementById('clear-form-btn').addEventListener('click', () => initNewInvoice())

// ════════════════════════════════════════════════════════════════
//  INVOICE LIST
// ════════════════════════════════════════════════════════════════

async function loadInvoices(params = {}) {
  try {
    const q = new URLSearchParams(params).toString()
    const list = await API.get('/invoices' + (q ? '?'+q : ''))
    renderInvoiceTable(list)
  } catch (e) {
    toast('Failed to load invoices: ' + e.message, 'error')
  }
}

function renderInvoiceTable(list) {
  const tbody = document.querySelector('#invoices-table tbody')
  const empty = document.getElementById('invoices-empty')

  if (!list || list.length === 0) {
    tbody.innerHTML = ''
    empty.style.display = 'block'
    return
  }
  empty.style.display = 'none'

  tbody.innerHTML = list.map(inv => `
    <tr>
      <td><strong>${inv.invoice_no}</strong></td>
      <td>${fmtDate(inv.date)}</td>
      <td>${inv.customer_name || '—'}</td>
      <td class="text-end">${fmtMoney(inv.taxable_amount)}</td>
      <td class="text-end">${fmtMoney(inv.cgst + inv.sgst + inv.igst)}</td>
      <td class="text-end fw-semibold text-primary">${fmtMoney(inv.grand_total)}</td>
      <td>${statusBadge(inv.status)}</td>
      <td class="text-center">
        <button class="btn-action view" title="View" onclick="viewInvoice(${inv.id})"><i class="fas fa-eye"></i></button>
        <button class="btn-action pdf"  title="PDF"  onclick="openInvoicePDF(${inv.id})"><i class="fas fa-file-pdf"></i></button>
        <button class="btn-action delete" title="Delete" onclick="deleteInvoice(${inv.id}, '${inv.invoice_no}')"><i class="fas fa-trash"></i></button>
      </td>
    </tr>
  `).join('')
}

// Filter bar
document.getElementById('inv-filter-btn').addEventListener('click', () => {
  const params = {}
  const s = document.getElementById('inv-search').value.trim()
  const f = document.getElementById('inv-filter-from').value
  const t = document.getElementById('inv-filter-to').value
  if (s) params.search = s
  if (f) params.start_date = f
  if (t) params.end_date = t
  loadInvoices(params)
})

document.getElementById('inv-search').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('inv-filter-btn').click()
})

// ── View invoice modal ────────────────────────────────────────────────────────
async function viewInvoice(id) {
  _viewInvoiceId = id
  try {
    const inv = await API.get(`/invoices/${id}`)
    document.getElementById('inv-view-title').textContent = `Invoice ${inv.invoice_no} — ${inv.customer_name}`

    const totalGST = inv.cgst + inv.sgst + inv.igst
    const itemsHtml = inv.items.map((it, i) => `
      <tr>
        <td>${i+1}</td>
        <td>${it.product_name}</td>
        <td>${it.hsn_code}</td>
        <td class="text-end">${parseFloat(it.qty).toFixed(0)}</td>
        <td class="text-end">${fmtMoney(it.rate)}</td>
        <td class="text-end">${it.gst_percent}%</td>
        <td class="text-end">${fmtMoney(it.taxable_amount)}</td>
        <td class="text-end fw-semibold">${fmtMoney(it.taxable_amount+it.cgst+it.sgst+it.igst)}</td>
      </tr>
    `).join('')

    const totalQty = inv.items.reduce((sum, it) => sum + parseFloat(it.qty || 0), 0)

    document.getElementById('inv-view-body').innerHTML = `
      <div class="row g-3 mb-3">
        <div class="col-md-4 inv-view-section"><div class="inv-view-label">Invoice No</div><div class="inv-view-value fw-bold">${inv.invoice_no}</div></div>
        <div class="col-md-4 inv-view-section"><div class="inv-view-label">Date</div><div class="inv-view-value">${fmtDate(inv.date)}</div></div>
        <div class="col-md-4 inv-view-section"><div class="inv-view-label">Type</div><div class="inv-view-value">${inv.invoice_type}</div></div>
        <div class="col-md-6 inv-view-section"><div class="inv-view-label">Customer</div><div class="inv-view-value fw-semibold">${inv.customer_name}</div><div class="text-muted small">${inv.customer_address||''}</div></div>
        <div class="col-md-3 inv-view-section"><div class="inv-view-label">GSTIN</div><div class="inv-view-value">${inv.customer_gstin||'—'}</div></div>
        <div class="col-md-3 inv-view-section"><div class="inv-view-label">Place of Supply</div><div class="inv-view-value">${inv.place_of_supply||'—'}</div></div>
      </div>
      <table class="table table-bordered table-sm mb-3">
        <thead class="table-dark">
          <tr><th>#</th><th>Product</th><th>HSN</th><th class="text-end">Qty</th><th class="text-end">Rate</th><th class="text-end">GST%</th><th class="text-end">Taxable</th><th class="text-end">Amount</th></tr>
        </thead>
        <tbody>${itemsHtml}</tbody>
        <tfoot class="table-light fw-bold">
          <tr>
            <td colspan="3" class="text-end">Total:</td>
            <td class="text-end">${totalQty.toFixed(0)}</td>
            <td colspan="4"></td>
          </tr>
        </tfoot>
      </table>
      <div class="row justify-content-end">
        <div class="col-md-5">
          <table class="table table-sm mb-2">
            <tr><td>Taxable Amount</td><td class="text-end">${fmtMoney(inv.taxable_amount)}</td></tr>
            ${inv.cgst > 0 ? `<tr><td>CGST</td><td class="text-end">${fmtMoney(inv.cgst)}</td></tr><tr><td>SGST</td><td class="text-end">${fmtMoney(inv.sgst)}</td></tr>` : ''}
            ${inv.igst > 0 ? `<tr><td>IGST</td><td class="text-end">${fmtMoney(inv.igst)}</td></tr>` : ''}
          </table>
          <div class="inv-view-total">${fmtMoney(inv.grand_total)}</div>
        </div>
      </div>
      <p class="text-muted mt-2 mb-0 small"><strong>Amount in words:</strong> ${numToWords(Math.round(inv.grand_total))}</p>
    `

    new bootstrap.Modal(document.getElementById('invoiceViewModal')).show()
  } catch (e) {
    toast('Could not load invoice: ' + e.message, 'error')
  }
}

document.getElementById('modal-pdf-btn').addEventListener('click', () => {
  if (_viewInvoiceId) openInvoicePDF(_viewInvoiceId)
})

// ── Delete invoice ─────────────────────────────────────────────────────────────
async function deleteInvoice(id, no) {
  const ok = window.electronAPI
    ? await window.electronAPI.confirm(`Delete invoice ${no}?`, 'This cannot be undone.')
    : confirm(`Delete invoice ${no}? This cannot be undone.`)
  if (!ok) return
  try {
    await API.delete(`/invoices/${id}`)
    toast(`Invoice ${no} deleted`, 'success')
    loadInvoices()
  } catch (e) {
    toast('Delete failed: ' + e.message, 'error')
  }
}
