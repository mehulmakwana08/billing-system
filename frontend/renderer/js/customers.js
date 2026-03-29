/* customers.js */

let _customerModal = null

async function loadCustomers() {
  try {
    const list = await refreshCustomers()
    renderCustomerTable(list)
    document.getElementById('cust-count').textContent = `Customers (${list.length})`
  } catch (e) {
    toast('Failed to load customers: ' + e.message, 'error')
  }
}

function renderCustomerTable(list) {
  const tbody = document.querySelector('#customers-table tbody')
  if (!list || list.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-4">No customers yet. Click "Add Customer" to get started.</td></tr>'
    return
  }
  tbody.innerHTML = list.map((c, i) => {
    const bal = c.balance || 0
    const status = c.balance_status || 'Settled'
    const statusMap = { 'Overpaid': 'bg-success', 'Settled': 'bg-primary', 'Due': 'bg-danger' }
    const balClass = bal > 0 ? 'text-success' : (bal < 0 ? 'text-danger' : '')
    const balPrefix = bal > 0 ? '+' : ''
    return `
    <tr>
      <td class="text-muted">${i+1}</td>
      <td>
        <div class="fw-semibold">${c.name}</div>
        ${c.address ? `<div class="text-muted small">${c.address.substring(0,60)}${c.address.length>60?'…':''}</div>` : ''}
      </td>
      <td><code>${c.gstin || '—'}</code></td>
      <td>${c.state_code || '—'}</td>
      <td>${c.phone || '—'}</td>
      <td class="${balClass} fw-semibold">${balPrefix}₹${Math.abs(bal).toFixed(2)}</td>
      <td><span class="badge ${statusMap[status] || 'bg-secondary'}">${status}</span></td>
      <td class="text-center">
        <button class="btn-action view" title="Ledger" onclick="showPage('ledger');loadLedger(${c.id})"><i class="fas fa-book"></i></button>
        <button class="btn-action edit" title="Edit" onclick="editCustomer(${c.id})"><i class="fas fa-edit"></i></button>
        <button class="btn-action delete" title="Delete" onclick="deleteCustomer(${c.id}, '${c.name.replace(/'/g,"\\'")}')"><i class="fas fa-trash"></i></button>
      </td>
    </tr>
  `}).join('')
}

// ── Add / Edit modal ──────────────────────────────────────────────────────────
function openCustomerModal(title = 'Add Customer') {
  document.getElementById('customerModalTitle').textContent = title
  if (!_customerModal) _customerModal = new bootstrap.Modal(document.getElementById('customerModal'))
  _customerModal.show()
}

function clearCustomerForm() {
  document.getElementById('cust-id').value       = ''
  document.getElementById('cust-name').value     = ''
  document.getElementById('cust-addr').value     = ''
  document.getElementById('cust-gstin-inp').value = ''
  document.getElementById('cust-state-inp').value = '24'
  document.getElementById('cust-phone').value    = ''
  document.getElementById('cust-email').value    = ''
}

document.getElementById('add-customer-btn').addEventListener('click', () => {
  clearCustomerForm()
  openCustomerModal('Add Customer')
})

async function editCustomer(id) {
  try {
    const c = await API.get(`/customers/${id}`)
    document.getElementById('cust-id').value        = c.id
    document.getElementById('cust-name').value      = c.name || ''
    document.getElementById('cust-addr').value      = c.address || ''
    document.getElementById('cust-gstin-inp').value  = c.gstin || ''
    document.getElementById('cust-state-inp').value  = c.state_code || '24'
    document.getElementById('cust-phone').value     = c.phone || ''
    document.getElementById('cust-email').value     = c.email || ''
    openCustomerModal('Edit Customer')
  } catch (e) {
    toast('Could not load customer: ' + e.message, 'error')
  }
}

// Auto-fill state code from GSTIN
document.getElementById('cust-gstin-inp').addEventListener('input', function () {
  if (this.value.length >= 2) {
    document.getElementById('cust-state-inp').value = this.value.substring(0, 2)
  }
})

// ── Save customer ─────────────────────────────────────────────────────────────
document.getElementById('save-customer-btn').addEventListener('click', async () => {
  const name = document.getElementById('cust-name').value.trim()
  if (!name) { toast('Customer name is required', 'error'); return }

  const payload = {
    name,
    address:    document.getElementById('cust-addr').value.trim(),
    gstin:      document.getElementById('cust-gstin-inp').value.trim().toUpperCase(),
    state_code: document.getElementById('cust-state-inp').value.trim(),
    phone:      document.getElementById('cust-phone').value.trim(),
    email:      document.getElementById('cust-email').value.trim(),
  }

  // Basic GSTIN validation
  if (payload.gstin && !/^\d{2}[A-Z]{5}\d{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/.test(payload.gstin)) {
    toast('GSTIN format is invalid (should be like 24XXXXX0000X1ZX)', 'error')
    return
  }

  const id = document.getElementById('cust-id').value
  try {
    if (id) {
      await API.put(`/customers/${id}`, payload)
      toast('Customer updated', 'success')
    } else {
      await API.post('/customers', payload)
      toast('Customer added', 'success')
    }
    _customerModal.hide()
    await loadCustomers()
  } catch (e) {
    toast('Save failed: ' + e.message, 'error')
  }
})

// ── Delete customer ───────────────────────────────────────────────────────────
async function deleteCustomer(id, name) {
  const ok = window.electronAPI
    ? await window.electronAPI.confirm(`Delete customer "${name}"?`, 'This cannot be undone.')
    : confirm(`Delete customer "${name}"?`)
  if (!ok) return
  try {
    await API.delete(`/customers/${id}`)
    toast('Customer deleted', 'success')
    await loadCustomers()
  } catch (e) {
    toast('Delete failed: ' + e.message, 'error')
  }
}
