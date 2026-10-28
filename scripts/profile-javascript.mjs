// Diagnostic only: profile the page and its existing dedicated workers.
import { mkdir, writeFile } from 'node:fs/promises'
import { createInterface } from 'node:readline'
import { once } from 'node:events'

const directory = process.argv[2]
const allocations = process.argv.includes('--allocations')
await mkdir(directory, { recursive: true })
const targets = await fetch('http://127.0.0.1:9222/json/list', { signal: AbortSignal.timeout(15000) }).then((r) => r.json())
const page = targets.find((target) => target.type === 'page')
if (!page) throw new Error('No page target for JavaScript profiling')
const socket = new WebSocket(page.webSocketDebuggerUrl)
await Promise.race([
  once(socket, 'open'),
  new Promise((_, reject) => setTimeout(() => reject(new Error('DevTools connection timed out')), 15000).unref()),
])
let nextId = 0
const requests = new Map()
const sessions = [{ sessionId: undefined, type: 'page', url: page.url }]
const starts = []
const traceEvents = []
let finishTrace
const traceComplete = new Promise((resolve) => { finishTrace = resolve })
const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  const id = ++nextId
  const timeout = setTimeout(() => {
    requests.delete(id)
    reject(new Error(`DevTools ${method} timed out`))
  }, 15000)
  requests.set(id, { resolve, reject, timeout })
  socket.send(JSON.stringify({ id, method, params, sessionId }))
})
const start = async (sessionId) => {
  if (allocations) {
    await send('HeapProfiler.startSampling', {
      samplingInterval: 32768,
      includeObjectsCollectedByMajorGC: true,
      includeObjectsCollectedByMinorGC: true,
    }, sessionId)
  } else {
    await send('Profiler.enable', {}, sessionId)
    await send('Profiler.setSamplingInterval', { interval: 100 }, sessionId)
    await send('Profiler.start', {}, sessionId)
  }
}
socket.addEventListener('message', ({ data }) => {
  const message = JSON.parse(data)
  if (message.id) {
    const request = requests.get(message.id)
    if (!request) return
    clearTimeout(request.timeout)
    requests.delete(message.id)
    if (message.error) request.reject(new Error(JSON.stringify(message.error)))
    else request.resolve(message.result)
  } else if (message.method === 'Tracing.dataCollected') {
    traceEvents.push(...message.params.value)
  } else if (message.method === 'Tracing.tracingComplete') {
    finishTrace()
  } else if (message.method === 'Target.attachedToTarget') {
    const { sessionId, targetInfo } = message.params
    sessions.push({ sessionId, type: targetInfo.type, url: targetInfo.url })
    starts.push(start(sessionId))
  }
})
if (!allocations) {
  await send('Tracing.start', { categories: 'devtools.timeline,cc,viz,disabled-by-default-devtools.timeline.invalidationTracking', transferMode: 'ReportEvents' })
}
await start()
await send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: false, flatten: true })
await Promise.all(starts)
process.stdout.write('ready\n')
const input = createInterface({ input: process.stdin })
await once(input, 'line')
input.close()
await Promise.all(starts)
for (const [index, session] of sessions.entries()) {
  const { profile } = await send(allocations ? 'HeapProfiler.stopSampling' : 'Profiler.stop', {}, session.sessionId)
  await writeFile(`${directory}/${allocations ? 'heap' : 'cpu'}-${index}-${session.type}.${allocations ? 'json' : 'cpuprofile'}`, JSON.stringify(profile))
}
if (!allocations) {
  await send('Tracing.end')
  await Promise.race([traceComplete, new Promise((_, reject) => setTimeout(() => reject(new Error('Browser trace did not finish')), 15000).unref())])
  await writeFile(`${directory}/browser-trace.json`, JSON.stringify({ traceEvents }))
}
const context = await send('Runtime.evaluate', { expression: `JSON.stringify({ aiNativeLayout: !!document.querySelector('.AiNativeLayout'), rootClass: document.documentElement.className, layoutRoots: [...document.querySelectorAll('.Workbench')].slice(0,20).map(root => { const ancestors=[]; for(let node=root;node;node=node.parentElement) ancestors.push({name:node.tagName,id:node.id,className:node.className}); return ancestors; }) })`, returnByValue: true })
await writeFile(`${directory}/layout-context.json`, context.result.value)
await writeFile(`${directory}/targets.json`, JSON.stringify(sessions, null, 2))
socket.close()
