const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const http = require('http')
const https = require('https')
const fs = require('fs')

const BACKEND_HOST = process.env.BILLING_BACKEND_HOST || '127.0.0.1'
const BACKEND_PORT = Number.parseInt(process.env.BILLING_BACKEND_PORT || '5000', 10)
const CLOUD_ONLY_MODE = process.env.BILLING_CLOUD_ONLY_MODE !== '0'
const DEFAULT_LIVE_BACKEND_ORIGIN = process.env.BILLING_LIVE_BACKEND_ORIGIN || 'https://billing-system-root.vercel.app'
const RAW_BACKEND_ORIGIN = process.env.BILLING_BACKEND_ORIGIN ||
  (CLOUD_ONLY_MODE ? DEFAULT_LIVE_BACKEND_ORIGIN : `http://${BACKEND_HOST}:${BACKEND_PORT}`)
const BACKEND_ORIGIN = RAW_BACKEND_ORIGIN.replace(/\/+$/, '')
const HEALTH_URL = `${BACKEND_ORIGIN}/api/health`
const USE_EXTERNAL_BACKEND = CLOUD_ONLY_MODE || process.env.BILLING_USE_EXTERNAL_BACKEND === '1'

let mainWindow
let flaskProcess

const hasSingleInstanceLock = app.requestSingleInstanceLock()
if (!hasSingleInstanceLock) {
  app.quit()
  process.exit(0)
}

app.on('second-instance', () => {
  if (!mainWindow) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.focus()
})

function probeFlask(timeoutMs = 1000) {
  return new Promise(resolve => {
    const client = HEALTH_URL.startsWith('https://') ? https : http
    const req = client.get(HEALTH_URL, res => {
      res.resume()
      resolve(res.statusCode === 200)
    })

    req.on('error', () => resolve(false))
    req.setTimeout(timeoutMs, () => {
      req.destroy()
      resolve(false)
    })
  })
}

// ── Find Python ──────────────────────────────────────────────────────────────
function getPython() {
  if (process.platform === 'win32') {
    const candidates = ['python', 'python3', 'py']
    return candidates[0]   // Windows usually has 'python'
  }
  return 'python3'
}

// ── Start Flask backend ──────────────────────────────────────────────────────
function startFlask() {
  const pythonExe = getPython()
  const backendDir = path.join(__dirname, '..', 'backend')
  const scriptPath = path.join(backendDir, 'app.py')

  console.log(`Starting Flask: ${pythonExe} ${scriptPath}`)

  flaskProcess = spawn(pythonExe, [scriptPath], {
    cwd: backendDir,
    stdio: ['ignore', 'pipe', 'pipe']
  })

  flaskProcess.stdout.on('data', d => console.log('[Flask]', d.toString().trim()))
  flaskProcess.stderr.on('data', d => console.log('[Flask-ERR]', d.toString().trim()))
  flaskProcess.on('exit', code => console.log('[Flask] exited with code', code))
}

// ── Wait for Flask ready ─────────────────────────────────────────────────────
function waitForFlask(callback, retries = 40, externalOnly = false) {
  const client = HEALTH_URL.startsWith('https://') ? https : http
  const req = client.get(HEALTH_URL, res => {
    if (res.statusCode === 200) {
      console.log(`[Electron] Backend is ready at ${BACKEND_ORIGIN}`)
      callback()
    } else {
      retry()
    }
  })
  req.on('error', retry)
  req.setTimeout(1000, () => { req.abort(); retry() })

  function retry() {
    if (retries > 0) {
      setTimeout(() => waitForFlask(callback, retries - 1, externalOnly), 500)
    } else {
      if (externalOnly) {
        console.error('[Electron] External backend is unavailable')
        dialog.showErrorBox(
          'Backend Error',
          'Desktop is running in cloud-only mode and an external backend is required.\n\n' +
          `Expected backend URL:\n  ${HEALTH_URL}\n\n` +
          'Check your internet connection and backend availability, then relaunch desktop.\n' +
          'You can also override the backend using BILLING_BACKEND_ORIGIN.'
        )
      } else {
        console.error('[Electron] Backend failed to start!')
        dialog.showErrorBox('Backend Error',
          'Could not start the billing backend.\n\n' +
          'Please ensure Python 3 is installed and run:\n' +
          '  pip install flask reportlab\n\n' +
          'Then restart the application.')
      }
    }
  }
}

// ── Create Window ─────────────────────────────────────────────────────────────
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1366,
    height: 820,
    minWidth: 1024,
    minHeight: 600,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    },
    title: 'Arvind Plastic Industries — Billing System',
    backgroundColor: '#F0F4F8',
    show: false
  })

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'))

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
  })

  // Open external links in system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http')) shell.openExternal(url)
    return { action: 'deny' }
  })
}

// ── IPC Handlers ─────────────────────────────────────────────────────────────

// Open PDF with system PDF viewer
ipcMain.handle('open-pdf', async (event, filePath) => {
  try {
    if (fs.existsSync(filePath)) {
      await shell.openPath(filePath)
      return { success: true }
    }
    return { success: false, error: 'File not found: ' + filePath }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// Save PDF to a chosen location
ipcMain.handle('save-pdf', async (event, { sourcePath, defaultName }) => {
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: defaultName,
    filters: [{ name: 'PDF Files', extensions: ['pdf'] }]
  })
  if (!result.canceled && result.filePath) {
    fs.copyFileSync(sourcePath, result.filePath)
    return { success: true, path: result.filePath }
  }
  return { success: false }
})

// Show confirmation dialog
ipcMain.handle('confirm', async (event, { message, detail }) => {
  const result = await dialog.showMessageBox(mainWindow, {
    type: 'question',
    buttons: ['Yes', 'Cancel'],
    defaultId: 0,
    title: 'Confirm',
    message,
    detail: detail || ''
  })
  return result.response === 0
})

// ── App lifecycle ─────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  const backendReady = await probeFlask()
  if (backendReady) {
    console.log(`[Electron] Reusing existing backend at ${BACKEND_ORIGIN}`)
  } else if (USE_EXTERNAL_BACKEND) {
    console.log(`[Electron] External backend mode enabled; waiting for ${BACKEND_ORIGIN}`)
  } else {
    startFlask()
  }

  waitForFlask(createWindow, 40, USE_EXTERNAL_BACKEND)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (flaskProcess) {
    flaskProcess.kill()
    console.log('[Electron] Flask process killed')
  }
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  if (flaskProcess) flaskProcess.kill()
})
