"""Diagnostic-only provider ablation; never used by the official report."""
from pathlib import Path
import sys


def disable_line_decorations(source):
    anchor = 'export const getEditorLineDecorations = async (editor: EditorState, rowIndex: number): Promise<readonly EditorLineDecoration[]> => {'
    if source.count(anchor) != 1 or "'onEditorLineDecoration'" not in source:
        raise RuntimeError('expected one known line-decoration provider implementation')
    return """import type { EditorLineDecoration } from '../EditorLineDecoration/EditorLineDecoration.ts'
import type { EditorState } from '../State/State.ts'
export const getEditorLineDecorations = async (_editor: EditorState, _rowIndex: number): Promise<readonly EditorLineDecoration[]> => []
"""


if __name__ == '__main__':
    target = Path(sys.argv[1])
    target.write_text(disable_line_decorations(target.read_text()))
