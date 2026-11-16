"use client";

import { useRef, useState } from "react";

/**
 * Wraps a table of sessions (rendered by the server) in a plain GET form that
 * posts the checked session ids to `/api/sessions/export`, which streams back
 * one JSON file with everything collected for each selected session.
 *
 * The form itself needs no JavaScript to work - it degrades to "check boxes,
 * submit, get a file". The client component only adds the "select all"
 * toggle and the live count on the button.
 */
export function SessionDownloadForm({ children }: { children: React.ReactNode }) {
  const formRef = useRef<HTMLFormElement>(null);
  const [selected, setSelected] = useState(0);

  function countChecked() {
    if (!formRef.current) return;
    setSelected(formRef.current.querySelectorAll('input[name="id"]:checked').length);
  }

  function toggleAll(event: React.ChangeEvent<HTMLInputElement>) {
    if (!formRef.current) return;
    formRef.current
      .querySelectorAll<HTMLInputElement>('input[name="id"]')
      .forEach((box) => {
        box.checked = event.target.checked;
      });
    countChecked();
  }

  return (
    <form
      ref={formRef}
      method="get"
      action="/api/sessions/export"
      target="_blank"
      onChange={countChecked}
    >
      <div className="download-bar">
        <label className="download-select-all">
          <input type="checkbox" onChange={toggleAll} aria-label="Selecionar todas" />
          selecionar todas
        </label>
        <button type="submit" disabled={selected === 0}>
          Baixar selecionadas {selected > 0 ? `(${selected})` : ""}
        </button>
      </div>
      {children}
    </form>
  );
}
