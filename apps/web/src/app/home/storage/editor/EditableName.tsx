"use client";

// Click-to-edit text input used for room / unit / section / slot names and
// the slot ``code``. The same shape works for all four levels because every
// rename is a PATCH followed by an optimistic update; the only knob the
// caller tweaks is ``maxLength``.

import { useEffect, useRef, useState } from "react";

export type EditableNameProps = {
  value: string;
  onCommit: (next: string) => Promise<void> | void;
  disabled?: boolean;
  maxLength?: number;
  // Visual hint: slot codes render as mono font in a small badge, not a
  // heading. The text-element defaults to <span>; ``as: "code"`` swaps in a
  // <code> tag with the right font.
  as?: "text" | "code";
  className?: string;
  // Show a spinner-ish overlay while the parent considers this field busy.
  busy?: boolean;
};

export function EditableName({
  value,
  onCommit,
  disabled,
  maxLength = 100,
  as = "text",
  className,
  busy,
}: EditableNameProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset the draft whenever the upstream value changes (e.g. another user
  // renamed the same node through a bulk operation). Without this the
  // editor would still show the old text after a successful rename.
  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);

  // Autofocus + select on enter so the user can type to overwrite.
  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        maxLength={maxLength}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={async () => {
          const trimmed = draft.trim();
          if (trimmed && trimmed !== value) {
            await onCommit(trimmed);
          }
          setEditing(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            inputRef.current?.blur();
          } else if (e.key === "Escape") {
            setDraft(value);
            setEditing(false);
          }
        }}
        className={`input h-7 text-sm ${as === "code" ? "font-mono" : ""} ${
          className ?? ""
        }`}
      />
    );
  }

  const Tag = as === "code" ? "code" : "span";
  return (
    <button
      type="button"
      onClick={() => {
        if (disabled) return;
        setDraft(value);
        setEditing(true);
      }}
      // The cursor hint only shows when the editor is interactive; the
      // busy overlay dims it so the user knows why clicks are inert.
      className={`group inline-flex items-center gap-1 rounded px-1 text-left transition-colors hover:bg-ink-50 ${
        disabled ? "cursor-default opacity-60" : "cursor-text"
      } ${className ?? ""}`}
      title={disabled ? "" : "点击改名"}
    >
      <Tag
        className={`${as === "code" ? "font-mono text-[12px]" : ""} ${
          busy ? "opacity-50" : ""
        }`}
      >
        {value}
      </Tag>
      <span
        aria-hidden
        className="text-[10px] text-ink-400 opacity-0 transition-opacity group-hover:opacity-100"
      >
        ✎
      </span>
    </button>
  );
}