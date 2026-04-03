/* products.js */

let _productModal = null
let _savingProduct = false

function logProducts(level, event, details = {}) {
  const logger = (typeof window !== 'undefined' && window.AppLogger) ? window.AppLogger : null
  if (!logger || typeof logger[level] !== 'function') return
  logger[level](event, details)
}

async function loadProducts() {
  try {
    logProducts('debug', 'products.load.start')
    const list = await refreshProducts()
    renderProductTable(list)
    document.getElementById('prod-count').textContent = `Products (${list.length})`
    logProducts('debug', 'products.load.success', { count: list.length })
  } catch (e) {
    logProducts('error', 'products.load.failed', { message: e.message })
    toast('Failed to load products: ' + e.message, 'error')
  }
}

function renderProductTable(list) {
  const tbody = document.querySelector('#products-table tbody')
  if (!list || list.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">No products yet. Click "Add Product" to get started.</td></tr>'
    return
  }
  tbody.innerHTML = list.map((p, i) => `
    <tr>
      <td class="text-muted">${i+1}</td>
      <td><div class="fw-semibold">${p.name}</div></td>
      <td><code>${p.hsn_code || '—'}</code></td>
      <td class="text-end">₹ ${parseFloat(p.default_rate || 0).toFixed(2)}</td>
      <td class="text-center"><span class="badge bg-info text-dark">${p.gst_percent}%</span></td>
      <td>${p.unit || 'PCS'}</td>
      <td class="text-center">
        <button class="btn-action edit" title="Edit" onclick="editProduct(${p.id})"><i class="fas fa-edit"></i></button>
        <button class="btn-action delete" title="Delete" onclick="deleteProduct(${p.id}, '${p.name.replace(/'/g,"\\'")}')"><i class="fas fa-trash"></i></button>
      </td>
    </tr>
  `).join('')
}

// ── Modal helpers ─────────────────────────────────────────────────────────────
function openProductModal(title = 'Add Product') {
  document.getElementById('productModalTitle').textContent = title
  if (!_productModal) _productModal = new bootstrap.Modal(document.getElementById('productModal'))
  _productModal.show()
}

function clearProductForm() {
  document.getElementById('prod-id').value   = ''
  document.getElementById('prod-name').value = ''
  document.getElementById('prod-hsn').value  = ''
  document.getElementById('prod-unit').value = 'PCS'
  document.getElementById('prod-rate').value = ''
  document.getElementById('prod-gst').value  = '18'
}

document.getElementById('add-product-btn').addEventListener('click', () => {
  clearProductForm()
  openProductModal('Add Product')
})

async function editProduct(id) {
  try {
    logProducts('debug', 'products.edit.load.start', { product_id: id })
    const p = await API.get(`/products/${id}`)
    document.getElementById('prod-id').value   = p.id
    document.getElementById('prod-name').value = p.name || ''
    document.getElementById('prod-hsn').value  = p.hsn_code || ''
    document.getElementById('prod-unit').value = p.unit || 'PCS'
    document.getElementById('prod-rate').value = p.default_rate || 0
    document.getElementById('prod-gst').value  = p.gst_percent || 18
    openProductModal('Edit Product')
    logProducts('debug', 'products.edit.load.success', { product_id: id })
  } catch (e) {
    logProducts('error', 'products.edit.load.failed', { product_id: id, message: e.message })
    toast('Could not load product: ' + e.message, 'error')
  }
}

// ── Save product ──────────────────────────────────────────────────────────────
async function saveProduct() {
  if (_savingProduct) return

  const saveBtn = document.getElementById('save-product-btn')
  const name = document.getElementById('prod-name').value.trim()
  if (!name) { toast('Product name is required', 'error'); return }

  const payload = {
    name,
    hsn_code:     document.getElementById('prod-hsn').value.trim(),
    unit:         document.getElementById('prod-unit').value.trim() || 'PCS',
    default_rate: parseFloat(document.getElementById('prod-rate').value) || 0,
    gst_percent:  parseFloat(document.getElementById('prod-gst').value)  || 18,
  }

  const id = document.getElementById('prod-id').value
  try {
    logProducts('info', 'products.save.start', {
      product_id: id ? Number(id) : null,
      action: id ? 'update' : 'create',
      name,
      gst_percent: payload.gst_percent,
    })
    _savingProduct = true
    if (saveBtn) saveBtn.disabled = true

    if (id) {
      await API.put(`/products/${id}`, payload)
      toast('Product updated', 'success')
    } else {
      await API.post('/products', payload)
      toast('Product added', 'success')
    }
    const activeEl = document.activeElement
    if (activeEl && typeof activeEl.blur === 'function') activeEl.blur()
    _productModal.hide()
    await loadProducts()
    logProducts('info', 'products.save.success', {
      product_id: id ? Number(id) : null,
      action: id ? 'update' : 'create',
      name,
    })
  } catch (e) {
    logProducts('error', 'products.save.failed', {
      product_id: id ? Number(id) : null,
      action: id ? 'update' : 'create',
      message: e.message,
    })
    toast('Save failed: ' + e.message, 'error')
  } finally {
    _savingProduct = false
    if (saveBtn) saveBtn.disabled = false
  }
}

document.getElementById('save-product-btn').addEventListener('click', saveProduct)
document.getElementById('product-form').addEventListener('submit', (e) => {
  e.preventDefault()
  saveProduct()
})

// ── Delete product ─────────────────────────────────────────────────────────────
async function deleteProduct(id, name) {
  const ok = window.electronAPI
    ? await window.electronAPI.confirm(`Delete product "${name}"?`, 'This cannot be undone.')
    : confirm(`Delete product "${name}"?`)
  if (!ok) return
  try {
    logProducts('warn', 'products.delete.start', { product_id: id, name })
    await API.delete(`/products/${id}`)
    logProducts('info', 'products.delete.success', { product_id: id, name })
    toast('Product deleted', 'success')
    await loadProducts()
  } catch (e) {
    logProducts('error', 'products.delete.failed', { product_id: id, name, message: e.message })
    toast('Delete failed: ' + e.message, 'error')
  }
}
