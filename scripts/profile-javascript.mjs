// Diagnostic only: profile the page and its existing dedicated workers.
import { mkdir, writeFile } from 'node:fs/promises'
import { createInterface } from 'node:readline'
import { once } from 'node:events'

const directory = process.argv[2]
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
  await send('Profiler.enable', {}, sessionId)
  await send('Profiler.start', {}, sessionId)
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
  } else if (message.method === 'Target.attachedToTarget') {
    const { sessionId, targetInfo } = message.params
    sessions.push({ sessionId, type: targetInfo.type, url: targetInfo.url })
    starts.push(start(sessionId))
  }
})
await start()
await send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: false, flatten: true })
await Promise.all(starts)
process.stdout.write('ready\n')
const input = createInterface({ input: process.stdin })
await once(input, 'line')
input.close()
await Promise.all(starts)
for (const [index, session] of sessions.entries()) {
  const { profile } = await send('Profiler.stop', {}, session.sessionId)
  await writeFile(`${directory}/cpu-${index}-${session.type}.cpuprofile`, JSON.stringify(profile))
}
await writeFile(`${directory}/targets.json`, JSON.stringify(sessions, null, 2))
socket.close()
