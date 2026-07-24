import { useEffect, useRef, useState } from "react";

type MenuItem = {
  label: string;
  onClick: () => void;
  danger?: boolean;
};

type Props = {
  items: MenuItem[];
};

export default function RowMenu({ items }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <div className="row-menu" ref={ref} onClick={(e) => e.stopPropagation()}>
      <button
        className="row-menu-trigger"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label="İşlemler"
      >
        ⋮
      </button>
      {open && (
        <div className="row-menu-dropdown">
          {items.map((item) => (
            <button
              key={item.label}
              className={item.danger ? "row-menu-danger" : undefined}
              onClick={() => {
                setOpen(false);
                item.onClick();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
