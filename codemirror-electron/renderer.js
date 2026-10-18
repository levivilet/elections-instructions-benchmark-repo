import {EditorState} from '@codemirror/state'
import {EditorView} from '@codemirror/view'

async function start() {
  const contents = await window.editor.read()
  const view = new EditorView({
    state: EditorState.create({doc: contents}),
    parent: document.querySelector('#editor'),
  })

  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault()
      void window.editor.write(view.state.doc.toString())
    }
  })
  window.editor.ready()
}

void start()
