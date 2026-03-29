const { contextBridge, ipcRenderer } = require('electron')

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
})
