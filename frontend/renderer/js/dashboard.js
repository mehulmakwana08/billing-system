/* dashboard.js */

let _revenueChart = null

async function loadDashboard() {
  try {
    const d = await API.get('/dashboard')

    // Stat cards
    document.getElementById('s-month-invoices').textContent = d.month_invoices
    document.getElementById('s-month-revenue').textContent  = fmtMoney(d.month_revenue)
    document.getElementById('s-month-gst').textContent      = fmtMoney(d.month_gst)
    document.getElementById('s-customers').textContent      = d.total_customers
    document.getElementById('s-total-invoices').textContent = d.total_invoices
    document.getElementById('s-total-revenue').textContent  = fmtMoney(d.total_revenue)
    document.getElementById('s-products').textContent       = d.total_products

    // Revenue chart
    const ctx = document.getElementById('revenue-chart').getContext('2d')
    const labels = d.monthly_chart.map(m => {
      const [y, mo] = m.month.split('-')
      return new Date(y, mo-1).toLocaleString('en-IN', { month: 'short', year: '2-digit' })
    })
    const revenues = d.monthly_chart.map(m => m.revenue)
    const gsts     = d.monthly_chart.map(m => m.gst)

    if (_revenueChart) _revenueChart.destroy()
    _revenueChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'Revenue (₹)',
            data: revenues,
            backgroundColor: 'rgba(30,77,140,0.80)',
            borderRadius: 6,
            borderSkipped: false,
          },
          {
            label: 'GST (₹)',
            data: gsts,
            backgroundColor: 'rgba(212,112,10,0.75)',
            borderRadius: 6,
            borderSkipped: false,
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: 'top', labels: { font: { size: 12 } } },
          tooltip: {
            callbacks: {
              label: ctx => ' ₹' + ctx.raw.toLocaleString('en-IN', { maximumFractionDigits: 0 })
            }
          }
        },
        scales: {
          y: {
            beginAtZero: true,
            ticks: {
              callback: v => '₹' + (v >= 100000
                ? (v/100000).toFixed(1) + 'L'
                : v.toLocaleString('en-IN'))
            },
            grid: { color: 'rgba(0,0,0,0.06)' }
          },
          x: { grid: { display: false } }
        }
      }
    })

    // Recent invoices
    const tbody = document.querySelector('#recent-table tbody')
    if (!d.recent_invoices || d.recent_invoices.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">No invoices yet. <a href="#" data-page="new-invoice">Create your first invoice →</a></td></tr>'
    } else {
      tbody.innerHTML = d.recent_invoices.map(inv => `
        <tr style="cursor:pointer" onclick="showPage('invoices')">
          <td><strong>${inv.invoice_no}</strong></td>
          <td>${fmtDate(inv.date)}</td>
          <td>${inv.customer_name || '—'}</td>
          <td class="text-end fw-semibold">${fmtMoney(inv.grand_total)}</td>
          <td>${statusBadge(inv.status)}</td>
        </tr>
      `).join('')
    }

    // Re-bind nav links in recent table
    tbody.querySelectorAll('[data-page]').forEach(el => {
      el.addEventListener('click', e => { e.preventDefault(); showPage(el.dataset.page) })
    })

  } catch (e) {
    console.error('Dashboard error:', e)
    toast('Could not load dashboard: ' + e.message, 'error')
  }
}
