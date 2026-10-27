const {contextBridge, ipcRenderer} = require('electron')

contextBridge.exposeInMainWorld('editor', {
  read: () => ipcRenderer.invoke('read-file'),
  ready: () => ipcRenderer.send('document-ready'),
  write: (contents) => ipcRenderer.invoke('write-file', contents),
})
