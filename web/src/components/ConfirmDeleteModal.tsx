import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { api } from "../api/client";
import Modal from "./Modal";

type Props = {
  title: string;
  description: ReactNode;
  warning?: ReactNode;
  onConfirm: () => void;
  onClose: () => void;
  isPending?: boolean;
  error?: string | null;
};

export default function ConfirmDeleteModal({
  title,
  description,
  warning,
  onConfirm,
  onClose,
  isPending = false,
  error,
}: Props) {
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.getSettings });
  const [confirmText, setConfirmText] = useState("");

  const businessName = settings.data?.business_name ?? "Hesaplık";
  const confirmWord = (businessName.trim().split(/\s+/)[0] ?? "").toLocaleUpperCase("tr");
  const valid = confirmText === confirmWord;

  function submit() {
    if (valid && !isPending) onConfirm();
  }

  return (
    <Modal title={title} onClose={onClose}>
      <div className="panel">{description}</div>

      {warning && (
        <p className="error" style={{ margin: "0 0 16px" }}>
          {warning}
        </p>
      )}

      <label className="field">
        <span>
          Silmek için <strong>{confirmWord}</strong> yazın
        </span>
        <input
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          placeholder={confirmWord}
          autoFocus
        />
      </label>

      {error && (
        <p className="error" style={{ margin: "12px 0 0" }}>
          {error}
        </p>
      )}

      <button
        className="danger"
        disabled={!valid || isPending}
        onClick={submit}
        style={{ marginTop: 16 }}
      >
        {isPending ? "Siliniyor…" : "Sil"}
      </button>
    </Modal>
  );
}
