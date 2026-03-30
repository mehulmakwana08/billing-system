const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const http = require('http')
const fs = require('fs')

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
    const req = http.get('http://localhost:5000/api/health', res => {
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
function waitForFlask(callback, retries = 40) {
  const req = http.get('http://localhost:5000/api/health', res => {
    if (res.statusCode === 200) {
      console.log('[Electron] Flask is ready')
      callback()
    } else {
      retry()
    }
  })
  req.on('error', retry)
  req.setTimeout(1000, () => { req.abort(); retry() })

  function retry() {
    if (retries > 0) {
      setTimeout(() => waitForFlask(callback, retries - 1), 500)
    } else {
      console.error('[Electron] Flask failed to start!')
      dialog.showErrorBox('Backend Error',
        'Could not start the billing backend.\n\n' +
        'Please ensure Python 3 is installed and run:\n' +
        '  pip install flask reportlab\n\n' +
        'Then restart the application.')
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
    console.log('[Electron] Reusing existing Flask backend on port 5000')
  } else {
    startFlask()
  }

  waitForFlask(createWindow)

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
