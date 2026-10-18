import ace from 'ace-builds/src-noconflict/ace'
import 'ace-builds/src-noconflict/theme-textmate'
import 'ace-builds/src-noconflict/mode-text'

async function start() {
  const contents = await window.editor.read()
  const editor = ace.edit(document.querySelector('#editor'))
  editor.setOptions({
    mode: 'ace/mode/text',
    theme: 'ace/theme/textmate',
    showLineNumbers: false,
    showGutter: false,
    useWorker: false,
    highlightActiveLine: false,
    highlightSelectedWord: false,
  })
  editor.setValue(contents, -1)

  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault()
      void window.editor.write(editor.getValue())
    }
  })
  window.editor.ready()
}

void start()
