const { contextBridge, ipcRenderer } = require('electron')

const BACKEND_HOST = process.env.BILLING_BACKEND_HOST || '127.0.0.1'
const BACKEND_PORT = Number.parseInt(process.env.BILLING_BACKEND_PORT || '5000', 10)
const CLOUD_ONLY_MODE = process.env.BILLING_CLOUD_ONLY_MODE !== '0'
const DEFAULT_LIVE_BACKEND_ORIGIN = process.env.BILLING_LIVE_BACKEND_ORIGIN || 'https://billing-system-root.vercel.app'
const RAW_BACKEND_ORIGIN = process.env.BILLING_BACKEND_ORIGIN ||
  (CLOUD_ONLY_MODE ? DEFAULT_LIVE_BACKEND_ORIGIN : `http://${BACKEND_HOST}:${BACKEND_PORT}`)
const BACKEND_ORIGIN = RAW_BACKEND_ORIGIN.replace(/\/+$/, '')

contextBridge.exposeInMainWorld('electronAPI', {
  // Open PDF file with system's default PDF viewer
  openPDF: (filePath) => ipcRenderer.invoke('open-pdf', filePath),

  // Save PDF to a user-chosen location
  savePDF: (sourcePath, defaultName) =>
    ipcRenderer.invoke('save-pdf', { sourcePath, defaultName }),

  // Native confirmation dialog
  confirm: (message, detail) =>
    ipcRenderer.invoke('confirm', { message, detail }),

  // Environment info
  platform: process.platform,
  backendOrigin: BACKEND_ORIGIN,
  getBackendOrigin: () => BACKEND_ORIGIN,
})
