/* products.js */

let _productModal = null

async function loadProducts() {
  try {
    const list = await refreshProducts()
    renderProductTable(list)
    document.getElementById('prod-count').textContent = `Products (${list.length})`
  } catch (e) {
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
    const p = await API.get(`/products/${id}`)
    document.getElementById('prod-id').value   = p.id
    document.getElementById('prod-name').value = p.name || ''
    document.getElementById('prod-hsn').value  = p.hsn_code || ''
    document.getElementById('prod-unit').value = p.unit || 'PCS'
    document.getElementById('prod-rate').value = p.default_rate || 0
    document.getElementById('prod-gst').value  = p.gst_percent || 18
    openProductModal('Edit Product')
  } catch (e) {
    toast('Could not load product: ' + e.message, 'error')
  }
}

// ── Save product ──────────────────────────────────────────────────────────────
document.getElementById('save-product-btn').addEventListener('click', async () => {
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
    if (id) {
      await API.put(`/products/${id}`, payload)
      toast('Product updated', 'success')
    } else {
      await API.post('/products', payload)
      toast('Product added', 'success')
    }
    _productModal.hide()
    await loadProducts()
  } catch (e) {
    toast('Save failed: ' + e.message, 'error')
  }
})

// ── Delete product ─────────────────────────────────────────────────────────────
async function deleteProduct(id, name) {
  const ok = window.electronAPI
    ? await window.electronAPI.confirm(`Delete product "${name}"?`, 'This cannot be undone.')
    : confirm(`Delete product "${name}"?`)
  if (!ok) return
  try {
    await API.delete(`/products/${id}`)
    toast('Product deleted', 'success')
    await loadProducts()
  } catch (e) {
    toast('Delete failed: ' + e.message, 'error')
  }
}
