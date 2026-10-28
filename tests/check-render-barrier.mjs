import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'

const calls = []
const callbacks = new Map()
let clock = 0
let sequence = 0
const context = vm.createContext({
  __esm: (entries) => Object.values(entries)[0],
  init_IsTest() {}, init_NormalizeRendererCommands() {}, init_RequestAnimationFrame() {}, init_Timestamp() {},
  isTest: () => false,
  now: () => ++clock,
  normalizeCommands: (commands) => commands,
  reset() {}, getCssText() {}, setCssText() {},
  requestAnimationFrame: (callback) => { callbacks.set(++sequence, callback); return sequence },
  cancelAnimationFrame: (id) => callbacks.delete(id),
  setTimeout, clearTimeout,
})
const patched = fs.readFileSync(process.argv[2], 'utf8')
const scheduler = patched.slice(patched.indexOf('// src/parts/RendererFrameScheduler/'), patched.indexOf('__export(ViewletEditorText_ipc_exports,'))
vm.runInContext(scheduler + '\ninit_RendererFrameScheduler();', context)
context.state3.rpc = { invoke: async (...args) => { calls.push(args) } }
await context.invoke('Before')
const background = context.sendMultiple([['background']])
await Promise.resolve()
assert.equal(callbacks.size, 1)
const lateFrame = [...callbacks.values()][0]
const pointer = context.sendMultiple([['pointer']], true)
const following = context.invoke('After')
await Promise.all([background, pointer, following])
assert.deepEqual(JSON.parse(JSON.stringify(calls)), [['Before'], ['Viewlet.sendMultiple', [['background'], ['pointer']]], ['After']])
lateFrame(100)
await Promise.resolve()
assert.equal(calls.length, 3)
assert.equal(callbacks.size, 0)
const ordinary = context.sendMultiple([['ordinary']])
await Promise.resolve()
assert.equal(callbacks.size, 1)
;[...callbacks.values()][0](200)
await ordinary
assert.equal(calls.length, 4)
