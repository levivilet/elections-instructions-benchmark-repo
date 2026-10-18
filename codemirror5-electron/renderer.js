import CodeMirror from 'codemirror'
import 'codemirror/lib/codemirror.css'

async function start() {
  const contents = await window.editor.read()
  const editor = CodeMirror(document.querySelector('#editor'), {
    value: contents,
    lineNumbers: false,
    lineWrapping: false,
    mode: null,
  })

  editor.focus()
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault()
      void window.editor.write(editor.getValue())
    }
  })
  window.editor.ready()
}

void start()
