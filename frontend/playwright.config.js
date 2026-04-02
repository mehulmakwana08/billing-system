const { defineConfig } = require('@playwright/test')

const shouldStartServer = process.env.E2E_SKIP_SERVER !== '1'

module.exports = defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:5000',
    headless: true,
    viewport: { width: 1366, height: 860 },
  },
  webServer: shouldStartServer
    ? {
        command: 'python ../backend/app.py',
        url: 'http://127.0.0.1:5000/api/health',
        reuseExistingServer: true,
        timeout: 120_000,
        env: {
          ...process.env,
          APP_MODE: 'cloud',
          AUTH_REQUIRED: '1',
          CLOUD_ONLY_MODE: '1',
          LOGIN_ONLY_MODE: '1',
          ALLOW_SELF_REGISTER: '0',
          DEFAULT_ADMIN_USERNAME: process.env.E2E_ADMIN_USERNAME || 'admin',
          DEFAULT_ADMIN_PASSWORD: process.env.E2E_ADMIN_PASSWORD || 'Admin@123',
          JWT_SECRET: process.env.E2E_JWT_SECRET || 'e2e-dev-secret',
        },
      }
    : undefined,
})
