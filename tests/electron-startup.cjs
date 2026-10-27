const assert = require('node:assert/strict')
const {EventEmitter} = require('node:events')
const fs = require('node:fs')
const path = require('node:path')
const {test} = require('node:test')
const vm = require('node:vm')

const root = path.resolve(__dirname, '..')
for (const [directory, name] of [
  ['basic-electron', 'Basic Electron Editor'],
  ['codemirror-electron', 'CodeMirror 6'],
  ['codemirror5-electron', 'CodeMirror 5'],
  ['ace-electron', 'Ace'],
]) {
  test(`${name} maps without a first-paint event and reports fixture readiness separately`, async () => {
    const app = new EventEmitter()
    app.whenReady = () => Promise.resolve()
    app.quit = () => {}
    app.exit = () => assert.fail('launcher failed')
    const ipcMain = new EventEmitter()
    ipcMain.handle = () => {}
    const windows = []
    class BrowserWindow extends EventEmitter {
      constructor(options) {
        super()
        this.visible = options.show !== false
        this.title = options.title
        this.webContents = new EventEmitter()
        windows.push(this)
      }
      loadFile() { return Promise.resolve() }
      show() { this.visible = true }
      setTitle(title) { this.title = title }
    }
    const main = path.join(root, directory, 'main.js')
    vm.runInNewContext(fs.readFileSync(main, 'utf8'), {
      require: (name) => name === 'electron' ? {app, BrowserWindow, ipcMain}
        : require(name),
      __dirname: path.dirname(main),
      process: {argv: ['electron', path.dirname(main), '/tmp/selection-fixture.txt']},
      console,
    }, {filename: main})
    await Promise.resolve()
    assert.equal(windows.length, 1)
    const window = windows[0]
    // A headless compositor need not supply a paint for a hidden window.
    // Never emit ready-to-show: native mapping must not depend on it.
    assert.equal(window.visible, true)
    assert.match(window.title, /Loading document$/)
    ipcMain.emit('document-ready', {sender: {}})
    assert.match(window.title, /Loading document$/)
    ipcMain.emit('document-ready', {sender: window.webContents})
    assert.equal(window.title, `${name} - selection-fixture.txt`)
  })
}

test('Basic Electron reports readiness only after assigning the loaded document', async () => {
  let resolveRead
  const textarea = {value: '', addEventListener() {}}
  const readyValues = []
  vm.runInNewContext(fs.readFileSync(path.join(root, 'basic-electron/renderer.js'), 'utf8'), {
    document: {querySelector: () => textarea},
    window: {editor: {
      read: () => new Promise((resolve) => { resolveRead = resolve }),
      ready: () => readyValues.push(textarea.value),
    }},
  })
  assert.deepEqual(readyValues, [])
  resolveRead('selectiontarget\n')
  // Drain the VM realm's promise continuations before observing readiness.
  await new Promise(setImmediate)
  assert.deepEqual(readyValues, ['selectiontarget\n'])
})
