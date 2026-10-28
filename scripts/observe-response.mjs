// Separate diagnostic: native input receipt to selection DOM and next frame.
// No CPU/heap profiler is enabled. Frame callbacks do not prove presentation.
import { once } from 'node:events'
import { writeFile } from 'node:fs/promises'
import { createInterface } from 'node:readline'

const [destination, editor] = process.argv.slice(2)
const targets = await fetch('http://127.0.0.1:9222/json/list', { signal: AbortSignal.timeout(15000) }).then((r) => r.json())
const page = targets.find((target) => target.type === 'page')
if (!page) throw new Error('No page target for response observation')
const socket = new WebSocket(page.webSocketDebuggerUrl)
await Promise.race([
  once(socket, 'open'),
  new Promise((_, reject) => setTimeout(() => reject(new Error('DevTools connection timed out')), 15000).unref()),
])
let nextId = 0
const requests = new Map()
const send = (method, params) => new Promise((resolve, reject) => {
  const id = ++nextId
  const timeout = setTimeout(() => {
    requests.delete(id)
    reject(new Error(`DevTools ${method} timed out`))
  }, 15000)
  requests.set(id, { resolve, reject, timeout })
  socket.send(JSON.stringify({ id, method, params }))
})
socket.addEventListener('message', ({ data }) => {
  const message = JSON.parse(data)
  const request = requests.get(message.id)
  if (!request) return
  clearTimeout(request.timeout)
  requests.delete(message.id)
  if (message.error) request.reject(new Error(JSON.stringify(message.error)))
  else request.resolve(message.result)
})
const selector = editor === 'lvce' ? '.EditorSelection' : '.CodeMirror-selected'
const install = await send('Runtime.evaluate', { expression: `(() => {
  const result = { input: null, mutation: null, frame: null, rectangles: [], inputTrusted: false, statusMutation: null, statusText: null };
  window.__selectionResponse = result;
  document.addEventListener('mousedown', (event) => {
    if (event.detail === 2 && result.input === null) {
      result.input = performance.now();
      result.inputTrusted = event.isTrusted;
    }
  }, {capture: true});
  const observer = new MutationObserver(() => {
    if (result.input === null || result.mutation !== null) return;
    const rectangles = Array.from(document.querySelectorAll(${JSON.stringify(selector)}), element => {
      const rect = element.getBoundingClientRect();
      return {width: rect.width, height: rect.height};
    }).filter(rect => rect.width > 0 && rect.height > 0);
    if (rectangles.length === 0) return;
    result.mutation = performance.now();
    result.rectangles = rectangles;
    observer.disconnect();
    requestAnimationFrame(() => {result.frame = performance.now();});
  });
  observer.observe(document.documentElement, {childList: true, attributes: true, subtree: true});
  if (${JSON.stringify(editor)} === 'lvce') {
    const statusObserver = new MutationObserver(() => {
      if (result.input === null || result.statusMutation !== null) return;
      const statusText = document.querySelector('[name="EditorPosition"]')?.textContent || '';
      if (!statusText.includes('(15 selected)')) return;
      result.statusMutation = performance.now();
      result.statusText = statusText;
      statusObserver.disconnect();
    });
    statusObserver.observe(document.documentElement, {childList: true, characterData: true, attributes: true, subtree: true});
  }
  return true;
})()`, returnByValue: true })
if (install.exceptionDetails || install.result.value !== true) throw new Error(JSON.stringify(install))
process.stdout.write('ready\n')
const input = createInterface({ input: process.stdin })
await once(input, 'line')
input.close()
const result = await send('Runtime.evaluate', { expression: 'window.__selectionResponse', returnByValue: true })
if (result.exceptionDetails) throw new Error(JSON.stringify(result))
const observation = result.result.value
if (!observation || ![observation.input, observation.mutation, observation.frame].every(Number.isFinite)
    || observation.mutation < observation.input || observation.frame < observation.mutation) {
  throw new Error(`Selection response was not observed: ${JSON.stringify(observation)}`)
}
if (editor === 'lvce' && (!Number.isFinite(observation.statusMutation)
    || observation.statusMutation < observation.input
    || observation.statusMutation - observation.input >= 1000)) {
  throw new Error(`Selected-word status did not update promptly: ${JSON.stringify(observation)}`)
}
await writeFile(destination, JSON.stringify({ ...observation,
  eventToStatusMilliseconds: Number.isFinite(observation.statusMutation) ? observation.statusMutation - observation.input : null,
  eventToMutationMilliseconds: observation.mutation - observation.input,
  eventToFrameMilliseconds: observation.frame - observation.input,
  note: 'Diagnostic page-clock observation; DOM and frame callbacks do not measure compositor presentation.',
}, null, 2))
socket.close()
