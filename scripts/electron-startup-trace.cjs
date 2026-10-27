// Temporary startup diagnostics; all events precede measured input.
module.exports = function traceStartup(app) {
  function trace(event, details = {}) {
    console.error('[DEBUG-wayland-startup]', JSON.stringify({
      pid: process.pid,
      monotonicSeconds: Number(process.hrtime.bigint()) / 1e9,
      wallTime: new Date().toISOString(),
      event,
      ...details,
    }))
  }
  trace('main-entry', {runtime: process.env.XDG_RUNTIME_DIR, display: process.env.WAYLAND_DISPLAY})
  app.once('ready', () => trace('app-ready'))
  app.on('child-process-gone', (_event, details) => trace('child-process-gone', details))
  app.on('browser-window-created', (_event, window) => {
    trace('window-created', {visible: window.isVisible()})
    for (const event of ['show', 'hide', 'ready-to-show', 'closed']) {
      window.once(event, () => trace(event))
    }
    for (const event of ['did-start-loading', 'dom-ready', 'did-finish-load', 'did-stop-loading']) {
      window.webContents.once(event, () => trace(event))
    }
    window.webContents.on('did-fail-load', (_event, code, description, url, isMainFrame) => {
      trace('did-fail-load', {code, description, url, isMainFrame})
    })
    window.webContents.on('render-process-gone', (_event, details) => trace('render-process-gone', details))
  })
  return trace
}
