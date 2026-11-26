"use client";

import { X } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef } from "react";

/**
 * Diálogo modal nativo (<dialog>): foco atrapado, Escape y fondo inerte los da el navegador.
 * El contenido solo existe mientras está abierto, así cada apertura empieza de cero.
 */
export function Dialog({
  open,
  title,
  closeLabel,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  closeLabel: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // Next.js oculta la página al navegar en vez de desmontarla: al volver, el diálogo cerrado.
  useLayoutEffect(() => () => close.current(), []);

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(e) => {
        // Clic en el fondo (fuera de la caja): el propio <dialog> es el destino.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {open && (
        <div className="dialog-box">
          <div className="dialog-head">
            <h2 id={titleId}>{title}</h2>
            <button
              type="button"
              className="icon-link"
              onClick={onClose}
              aria-label={closeLabel}
              title={closeLabel}
            >
              <X aria-hidden="true" size={20} />
            </button>
          </div>
          {children}
        </div>
      )}
    </dialog>
  );
}
