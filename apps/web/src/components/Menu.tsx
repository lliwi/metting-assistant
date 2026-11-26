"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";

export type MenuItem = {
  key: string;
  label: string;
  icon?: React.ReactNode;
  danger?: boolean;
};

/** Menú desplegable de acciones (patrón "menu button" de WAI-ARIA). */
export function Menu({
  label,
  icon,
  items,
  onSelect,
}: {
  label: string;
  icon: React.ReactNode;
  items: MenuItem[];
  onSelect: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const menuId = useId();

  // Transitorio: si Next.js oculta la página al navegar, al volver estará cerrado.
  useLayoutEffect(() => () => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    list.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const outside = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  if (items.length === 0) return null;

  function onKeyDown(e: React.KeyboardEvent) {
    const buttons = [
      ...(list.current?.querySelectorAll<HTMLButtonElement>("button") ?? []),
    ];
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const move = (to: number) =>
      buttons[(to + buttons.length) % buttons.length]?.focus();
    if (e.key === "ArrowDown") move(current + 1);
    else if (e.key === "ArrowUp") move(current - 1);
    else if (e.key === "Home") move(0);
    else if (e.key === "End") move(buttons.length - 1);
    else if (e.key === "Escape") {
      setOpen(false);
      root.current?.querySelector<HTMLButtonElement>("button")?.focus();
    } else if (e.key === "Tab") setOpen(false);
    else return;
    e.preventDefault();
  }

  return (
    <div className="menu" ref={root}>
      <button
        type="button"
        className="button icon-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={label}
        title={label}
        onClick={() => setOpen((o) => !o)}
      >
        {icon}
      </button>
      {open && (
        <ul
          ref={list}
          id={menuId}
          role="menu"
          aria-label={label}
          className="menu-list"
          onKeyDown={onKeyDown}
        >
          {items.map((item) => (
            <li key={item.key} role="none">
              <button
                type="button"
                role="menuitem"
                className={item.danger ? "menu-item danger" : "menu-item"}
                onClick={() => {
                  setOpen(false);
                  onSelect(item.key);
                }}
              >
                {item.icon}
                {item.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
