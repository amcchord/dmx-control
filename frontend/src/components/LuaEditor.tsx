import React, { useEffect, useRef } from "react";
import { EditorState, Compartment, StateEffect } from "@codemirror/state";
import {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLine,
  highlightActiveLineGutter,
  drawSelection,
  Decoration,
  DecorationSet,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import {
  bracketMatching,
  defaultHighlightStyle,
  indentOnInput,
  StreamLanguage,
  syntaxHighlighting,
} from "@codemirror/language";
import { lua } from "@codemirror/legacy-modes/mode/lua";
import { oneDark } from "@codemirror/theme-one-dark";
import { StateField } from "@codemirror/state";

type Props = {
  value: string;
  onChange?: (value: string) => void;
  errorLine?: number | null;
  readOnly?: boolean;
  className?: string;
};

const setErrorLine = StateEffect.define<number | null>();

const errorLineField = StateField.define<DecorationSet>({
  create() {
    return Decoration.none;
  },
  update(deco, tr) {
    let next = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (e.is(setErrorLine)) {
        if (e.value == null) {
          next = Decoration.none;
        } else {
          const linePos = tr.state.doc.line(
            Math.max(1, Math.min(e.value, tr.state.doc.lines)),
          );
          next = Decoration.set([
            Decoration.line({
              attributes: {
                class: "cm-error-line",
              },
            }).range(linePos.from),
          ]);
        }
      }
    }
    return next;
  },
  provide(field) {
    return EditorView.decorations.from(field);
  },
});

export default function LuaEditor({
  value,
  onChange,
  errorLine,
  readOnly,
  className,
}: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const readOnlyCompRef = useRef(new Compartment());

  useEffect(() => {
    if (!hostRef.current || viewRef.current) return;
    const updateListener = EditorView.updateListener.of((u) => {
      if (u.docChanged && onChangeRef.current) {
        onChangeRef.current(u.state.doc.toString());
      }
    });
    const state = EditorState.create({
      doc: value,
      extensions: [
        lineNumbers(),
        highlightActiveLineGutter(),
        history(),
        drawSelection(),
        indentOnInput(),
        bracketMatching(),
        highlightActiveLine(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        StreamLanguage.define(lua),
        oneDark,
        keymap.of([...defaultKeymap, ...historyKeymap]),
        readOnlyCompRef.current.of(EditorState.readOnly.of(!!readOnly)),
        errorLineField,
        updateListener,
        EditorView.theme({
          "&": { height: "100%" },
          ".cm-scroller": { fontFamily: "ui-monospace, monospace" },
          ".cm-content": { fontSize: "12px" },
          ".cm-error-line": {
            backgroundColor: "rgba(244, 63, 94, 0.18)",
            outline: "1px solid rgba(244, 63, 94, 0.6)",
          },
        }),
      ],
    });
    const view = new EditorView({ state, parent: hostRef.current });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync external value changes (e.g. loading a preset) into the editor.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current === value) return;
    view.dispatch({
      changes: { from: 0, to: current.length, insert: value },
    });
  }, [value]);

  // Sync error line decoration.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: setErrorLine.of(errorLine ?? null),
    });
  }, [errorLine]);

  // Toggle read-only without recreating the editor.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: readOnlyCompRef.current.reconfigure(
        EditorState.readOnly.of(!!readOnly),
      ),
    });
  }, [readOnly]);

  return (
    <div
      ref={hostRef}
      className={
        "h-full w-full overflow-hidden rounded-md ring-1 ring-line " +
        (className ?? "")
      }
    />
  );
}
