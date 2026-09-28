"use client";

// Two-step delete confirm.
//
// The first click puts the button into an "armed" state ("再点一次确认")
// which auto-resets after 3 s. A second click within that window actually
// runs the delete. No modal, no ``window.confirm`` — keeping the editor
// entirely inline avoids the scroll-jump a modal triggers inside a tree.

import { useEffect, useState } from "react";

export type ConfirmDeleteProps = {
  label?: string;
  onConfirm: () => Promise<void> | void;
  disabled?: boolean;
  // Render a smaller variant suited for leaf rows (slots). The default
  // size matches room/unit/section rows.
  size?: "sm" | "md";
};

export function ConfirmDelete({
  label = "删除",
  onConfirm,
  disabled,
  size = "md",
}: ConfirmDeleteProps) {
  const [armed, setArmed] = useState(false);

  // Auto-disarm after 3 s so a stray first click doesn't leave a confusing
  // "确认" button sitting there waiting for someone to step on it.
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 3000);
    return () => clearTimeout(t);
  }, [armed]);

  const sizeClasses =
    size === "sm"
      ? "text-[10px] px-1.5 py-0.5"
      : "text-xs px-2 py-0.5";

  if (armed) {
    return (
      <button
        type="button"
        disabled={disabled}
        onClick={async () => {
          await onConfirm();
          setArmed(false);
        }}
        className={`rounded font-semibold text-red-600 bg-red-50 hover:bg-red-100 transition-colors ${sizeClasses}`}
      >
        再点一次确认
      </button>
    );
  }

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => setArmed(true)}
      className={`rounded text-red-500 hover:text-red-700 hover:bg-red-50 transition-colors ${sizeClasses}`}
    >
      {label}
    </button>
  );
}