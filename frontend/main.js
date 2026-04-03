const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const http = require('http')
const https = require('https')
const fs = require('fs')
const log = require('electron-log')

function envInt(value, fallback) {
  const parsed = Number.parseInt(value, 10)
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback
  return parsed
}

const LOG_LEVEL = String(process.env.BILLING_LOG_LEVEL || 'debug').toLowerCase()
const LOG_RETENTION_DAYS = envInt(process.env.BILLING_LOG_RETENTION_DAYS || '7', 7)
const LOG_MAX_TOTAL_SIZE = envInt(process.env.BILLING_LOG_MAX_TOTAL_SIZE_BYTES || `${50 * 1024 * 1024}`, 50 * 1024 * 1024)
const LOG_MAX_FILE_SIZE = envInt(process.env.BILLING_LOG_MAX_SIZE_BYTES || `${5 * 1024 * 1024}`, 5 * 1024 * 1024)
const SENSITIVE_KEY_PATTERN = /pass(word)?|token|authorization|cookie|secret|jwt/i

function redactString(value) {
  return String(value)
    .replace(/Bearer\s+[A-Za-z0-9\-._~+/]+=*/gi, 'Bearer [REDACTED]')
    .replace(/(["']?(?:password|token|authorization|cookie|secret)["']?\s*[:=]\s*)[^,\s}]+/gi, '$1[REDACTED]')
}

function sanitizeForLog(value, depth = 0) {
  if (value === null || value === undefined) return value
  if (depth > 4) return '[TRUNCATED]'

  if (Array.isArray(value)) {
    return value.slice(0, 30).map((entry) => sanitizeForLog(entry, depth + 1))
  }

  if (typeof value === 'object') {
    const out = {}
    Object.entries(value).slice(0, 50).forEach(([key, entry]) => {
      if (SENSITIVE_KEY_PATTERN.test(key)) {
        if ((key.startsWith('has_') || key.startsWith('is_')) && typeof entry === 'boolean') {
          out[key] = entry
        } else {
          out[key] = '[REDACTED]'
        }
      } else {
        out[key] = sanitizeForLog(entry, depth + 1)
      }
    })
    return out
  }

  if (typeof value === 'string') {
    return redactString(value).slice(0, 2000)
  }

  return value
}

function cleanupOldLogs(logDir) {
  try {
    const now = Date.now()
    const maxAgeMs = Math.max(LOG_RETENTION_DAYS, 1) * 24 * 60 * 60 * 1000
    const entries = fs
      .readdirSync(logDir)
      .map((name) => {
        const fullPath = path.join(logDir, name)
        const stats = fs.statSync(fullPath)
        return { name, fullPath, stats }
      })
      .filter((entry) => entry.stats.isFile())

    entries.forEach((entry) => {
      if (entry.name === 'desktop.log') return
      if (now - entry.stats.mtimeMs > maxAgeMs) {
        try {
          fs.unlinkSync(entry.fullPath)
        } catch (_ignored) {
          // Ignore cleanup failures; logging should keep running.
        }
      }
    })

    let activeEntries = fs
      .readdirSync(logDir)
      .map((name) => {
        const fullPath = path.join(logDir, name)
        const stats = fs.statSync(fullPath)
        return { name, fullPath, stats }
      })
      .filter((entry) => entry.stats.isFile())
      .sort((a, b) => a.stats.mtimeMs - b.stats.mtimeMs)

    let totalSize = activeEntries.reduce((sum, entry) => sum + entry.stats.size, 0)
    while (totalSize > LOG_MAX_TOTAL_SIZE && activeEntries.length > 1) {
      const removable = activeEntries.find((entry) => entry.name !== 'desktop.log')
      if (!removable) break
      try {
        fs.unlinkSync(removable.fullPath)
      } catch (_ignored) {
        break
      }
      totalSize -= removable.stats.size
      activeEntries = activeEntries.filter((entry) => entry.fullPath !== removable.fullPath)
    }
  } catch (_ignored) {
    // Logging setup should not block app startup.
  }
}

function logEvent(level, eventName, details) {
  const logger = typeof log[level] === 'function' ? log[level] : log.info
  if (details === undefined) {
    logger.call(log, eventName)
    return
  }

  try {
    const safe = sanitizeForLog(details)
    logger.call(log, `${eventName} ${JSON.stringify(safe)}`)
  } catch (_err) {
    logger.call(log, eventName)
  }
}

function configureDesktopLogger() {
  try {
    const logDir = path.join(app.getPath('userData'), 'logs')
    fs.mkdirSync(logDir, { recursive: true })

    log.transports.console.level = false
    log.transports.file.level = LOG_LEVEL
    log.transports.file.maxSize = Math.max(LOG_MAX_FILE_SIZE, 1024 * 1024)
    log.transports.file.resolvePathFn = () => path.join(logDir, 'desktop.log')
    log.transports.file.format = '[{y}-{m}-{d} {h}:{i}:{s}.{ms}] [{level}] {text}'

    cleanupOldLogs(logDir)
    logEvent('info', 'logger.initialized', {
      level: LOG_LEVEL,
      log_dir: logDir,
      retention_days: LOG_RETENTION_DAYS,
    })
  } catch (err) {
    // Fallback to stderr if file logging initialization fails.
    process.stderr.write(`Logger setup failed: ${err.message}\n`)
  }
}

configureDesktopLogger()

process.on('uncaughtException', (err) => {
  logEvent('error', 'process.uncaught_exception', { message: err.message, stack: err.stack })
})

process.on('unhandledRejection', (reason) => {
  logEvent('error', 'process.unhandled_rejection', {
    reason: reason && reason.message ? reason.message : String(reason),
    stack: reason && reason.stack ? reason.stack : '',
  })
})

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
  logEvent('warn', 'app.second_instance_blocked')
  app.quit()
  process.exit(0)
}

app.on('second-instance', () => {
  logEvent('info', 'app.second_instance_focus_request')
  if (!mainWindow) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.focus()
})

function probeFlask(timeoutMs = 1000) {
  return new Promise(resolve => {
    const client = HEALTH_URL.startsWith('https://') ? https : http
    const req = client.get(HEALTH_URL, res => {
      res.resume()
      resolve({ ok: res.statusCode === 200, statusCode: res.statusCode })
    })

    req.on('error', (err) => resolve({ ok: false, error: err.message }))
    req.setTimeout(timeoutMs, () => {
      req.destroy()
      resolve({ ok: false, error: 'timeout' })
    })
  })
}

// ── Find Python ──────────────────────────────────────────────────────────────
function getPython() {
  if (process.platform === 'win32') {
    const candidates = ['python', 'python3', 'py']
    logEvent('debug', 'backend.python_candidates', { candidates })
    return candidates[0]   // Windows usually has 'python'
  }
  return 'python3'
}

// ── Start Flask backend ──────────────────────────────────────────────────────
function startFlask() {
  const pythonExe = getPython()
  const backendDir = path.join(__dirname, '..', 'backend')
  const scriptPath = path.join(backendDir, 'app.py')

  logEvent('info', 'backend.spawn.start', {
    python: pythonExe,
    script_path: scriptPath,
    backend_dir: backendDir,
  })

  flaskProcess = spawn(pythonExe, [scriptPath], {
    cwd: backendDir,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    env: {
      ...process.env,
      BILLING_LOG_LEVEL: process.env.BILLING_BACKEND_LOG_LEVEL || 'DEBUG',
      PYTHONUNBUFFERED: '1',
    },
  })

  logEvent('info', 'backend.spawn.started', { pid: flaskProcess.pid || null })

  flaskProcess.stdout.on('data', (chunk) => {
    const lines = String(chunk).split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
    lines.forEach((line) => logEvent('debug', 'backend.stdout', { line }))
  })

  flaskProcess.stderr.on('data', (chunk) => {
    const lines = String(chunk).split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
    lines.forEach((line) => logEvent('warn', 'backend.stderr', { line }))
  })

  flaskProcess.on('exit', (code, signal) => {
    logEvent('warn', 'backend.process_exit', { code, signal: signal || '' })
    flaskProcess = null
  })
}

// ── Wait for Flask ready ─────────────────────────────────────────────────────
async function waitForFlask(callback, retries = 40, externalOnly = false, attempt = 1) {
  const probe = await probeFlask(1000)
  if (probe.ok) {
    logEvent('info', 'backend.ready', {
      origin: BACKEND_ORIGIN,
      status_code: probe.statusCode,
      attempt,
    })
    callback()
    return
  }

  if (retries > 0) {
    logEvent('debug', 'backend.wait_retry', {
      attempt,
      retries_remaining: retries,
      reason: probe.error || `status_${probe.statusCode || 'unknown'}`,
    })
    setTimeout(() => {
      waitForFlask(callback, retries - 1, externalOnly, attempt + 1)
    }, 500)
    return
  }

  if (externalOnly) {
    logEvent('error', 'backend.external_unavailable', { health_url: HEALTH_URL })
    dialog.showErrorBox(
      'Backend Error',
      'Desktop is running in cloud-only mode and an external backend is required.\n\n' +
      `Expected backend URL:\n  ${HEALTH_URL}\n\n` +
      'Check your internet connection and backend availability, then relaunch desktop.\n' +
      'You can also override the backend using BILLING_BACKEND_ORIGIN.'
    )
  } else {
    logEvent('error', 'backend.start_failed', {
      health_url: HEALTH_URL,
      final_reason: probe.error || `status_${probe.statusCode || 'unknown'}`,
    })
    dialog.showErrorBox('Backend Error',
      'Could not start the billing backend.\n\n' +
      'Please ensure Python 3 is installed and run:\n' +
      '  pip install flask reportlab\n\n' +
      'Then restart the application.')
  }
}

// ── Create Window ─────────────────────────────────────────────────────────────
function createWindow() {
  logEvent('info', 'window.create.start', {
    width: 1366,
    height: 820,
    preload: path.join(__dirname, 'preload.js'),
  })

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
    logEvent('info', 'window.ready_to_show')
    mainWindow.show()
  })

  mainWindow.webContents.on('did-finish-load', () => {
    logEvent('info', 'window.did_finish_load')
  })

  // Open external links in system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    logEvent('debug', 'window.external_open_request', { url })
    if (url.startsWith('http')) shell.openExternal(url)
    return { action: 'deny' }
  })
}

// ── IPC Handlers ─────────────────────────────────────────────────────────────

// Open PDF with system PDF viewer
ipcMain.handle('open-pdf', async (event, filePath) => {
  try {
    logEvent('debug', 'ipc.open_pdf.request', { file_name: path.basename(filePath || '') })
    if (fs.existsSync(filePath)) {
      await shell.openPath(filePath)
      logEvent('info', 'ipc.open_pdf.success', { file_name: path.basename(filePath || '') })
      return { success: true }
    }
    logEvent('warn', 'ipc.open_pdf.missing', { file_name: path.basename(filePath || '') })
    return { success: false, error: 'File not found: ' + filePath }
  } catch (err) {
    logEvent('error', 'ipc.open_pdf.error', { message: err.message })
    return { success: false, error: err.message }
  }
})

// Save PDF to a chosen location
ipcMain.handle('save-pdf', async (event, { sourcePath, defaultName }) => {
  logEvent('debug', 'ipc.save_pdf.request', {
    source_name: path.basename(sourcePath || ''),
    default_name: defaultName || '',
  })
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: defaultName,
    filters: [{ name: 'PDF Files', extensions: ['pdf'] }]
  })
  if (!result.canceled && result.filePath) {
    fs.copyFileSync(sourcePath, result.filePath)
    logEvent('info', 'ipc.save_pdf.success', { target_name: path.basename(result.filePath || '') })
    return { success: true, path: result.filePath }
  }
  logEvent('debug', 'ipc.save_pdf.cancelled')
  return { success: false }
})

// Show confirmation dialog
ipcMain.handle('confirm', async (event, { message, detail }) => {
  logEvent('debug', 'ipc.confirm.request', {
    message: message || '',
    detail: detail || '',
  })
  const result = await dialog.showMessageBox(mainWindow, {
    type: 'question',
    buttons: ['Yes', 'Cancel'],
    defaultId: 0,
    title: 'Confirm',
    message,
    detail: detail || ''
  })
  logEvent('debug', 'ipc.confirm.response', { accepted: result.response === 0 })
  return result.response === 0
})

ipcMain.on('renderer-log', (_event, payload = {}) => {
  const level = String(payload.level || 'info').toLowerCase()
  const eventName = String(payload.event || 'event')
  const details = payload.details || {}
  logEvent(level, `renderer.${eventName}`, details)
})

ipcMain.handle('get-log-path', async () => {
  try {
    const file = log.transports.file.getFile()
    return file && file.path ? file.path : ''
  } catch (_ignored) {
    return ''
  }
})

// ── App lifecycle ─────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  logEvent('info', 'app.ready', {
    cloud_only_mode: CLOUD_ONLY_MODE,
    use_external_backend: USE_EXTERNAL_BACKEND,
    backend_origin: BACKEND_ORIGIN,
  })

  const backendProbe = await probeFlask()
  if (backendProbe.ok) {
    logEvent('info', 'backend.reuse_existing', {
      origin: BACKEND_ORIGIN,
      status_code: backendProbe.statusCode,
    })
  } else if (USE_EXTERNAL_BACKEND) {
    logEvent('info', 'backend.external_wait', {
      origin: BACKEND_ORIGIN,
      reason: backendProbe.error || 'unavailable',
    })
  } else {
    startFlask()
  }

  waitForFlask(createWindow, 40, USE_EXTERNAL_BACKEND)

  app.on('activate', () => {
    logEvent('debug', 'app.activate')
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  logEvent('info', 'app.window_all_closed')
  if (flaskProcess) {
    try {
      flaskProcess.kill()
      logEvent('info', 'backend.kill.window_all_closed')
    } catch (err) {
      logEvent('warn', 'backend.kill.window_all_closed_failed', { message: err.message })
    }
  }
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  logEvent('info', 'app.before_quit')
  if (flaskProcess) {
    try {
      flaskProcess.kill()
      logEvent('debug', 'backend.kill.before_quit')
    } catch (err) {
      logEvent('warn', 'backend.kill.before_quit_failed', { message: err.message })
    }
  }
})
