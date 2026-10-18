const {contextBridge, ipcRenderer} = require('electron')

contextBridge.exposeInMainWorld('editor', {
  read: () => ipcRenderer.invoke('read-file'),
  write: (contents) => ipcRenderer.invoke('write-file', contents),
  ready: () => ipcRenderer.send('document-ready'),
})
