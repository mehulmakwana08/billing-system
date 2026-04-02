const { test, expect } = require('@playwright/test')

const TEST_USERNAME = process.env.E2E_ADMIN_USERNAME || 'admin'
const TEST_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'Admin@123'

async function login(page) {
  await expect(page.locator('#authModal')).toBeVisible()
  await page.fill('#login-username', TEST_USERNAME)
  await page.fill('#login-password', TEST_PASSWORD)
  await page.click('#login-btn')
  await expect(page.locator('#authModal')).toBeHidden()
}

test.beforeEach(async ({ page }) => {
  page.on('dialog', async (dialog) => {
    await dialog.dismiss()
  })
  await page.goto('/')
  await login(page)
})

test('dashboard loads and shows stats cards', async ({ page }) => {
  await expect(page.locator('#page-dashboard')).toBeVisible()
  await expect(page.locator('#s-month-invoices')).toBeVisible()
  await expect(page.locator('#s-total-revenue')).toBeVisible()
})

test('reports init does not duplicate year options', async ({ page }) => {
  await page.click('[data-page="reports"]')
  const firstCount = await page.locator('#report-year option').count()

  await page.click('[data-page="dashboard"]')
  await page.click('[data-page="reports"]')
  const secondCount = await page.locator('#report-year option').count()

  expect(secondCount).toBe(firstCount)
})

test('can create invoice from seeded data path', async ({ page }) => {
  await page.click('[data-page="new-invoice"]')

  await page.selectOption('#customer-select', { index: 1 })
  await page.selectOption('#items-body .item-product', { index: 1 })
  await page.fill('#items-body .item-qty', '2')
  await page.fill('#items-body .item-rate', '120')

  await page.click('#invoice-form button[type="submit"]')
  await expect(page.locator('#toast-msg')).toContainText('saved', { timeout: 15_000 })
})
