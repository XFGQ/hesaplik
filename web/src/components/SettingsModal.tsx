import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { useToast } from "../lib/toast";
import Modal from "./Modal";

type Props = {
  onClose: () => void;
};

export default function SettingsModal({ onClose }: Props) {
  const qc = useQueryClient();
  const toast = useToast();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.getSettings });
  const [businessName, setBusinessName] = useState("");

  useEffect(() => {
    if (settings.data) setBusinessName(settings.data.business_name ?? "");
  }, [settings.data]);

  const save = useMutation({
    mutationFn: () => api.updateSetting("business_name", businessName.trim()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      toast("Ayarlar güncellendi", "success");
      onClose();
    },
  });

  const valid = businessName.trim().length >= 2;

  return (
    <Modal title="Ayarlar" onClose={onClose}>
      <div className="panel">
        <p className="panel-title">İşletme</p>
        <label className="field">
          <span>İşletme adı</span>
          <input
            value={businessName}
            onChange={(e) => setBusinessName(e.target.value)}
            placeholder="Hesaplık"
            autoFocus
          />
        </label>
      </div>

      <div className="panel">
        <p className="panel-title">Yedekleme</p>
        <p className="muted">Yakında</p>
      </div>

      {save.isError && (
        <p className="error" style={{ margin: "0 0 12px" }}>
          {(save.error as Error).message}
        </p>
      )}

      <button
        className="primary"
        disabled={!valid || save.isPending}
        onClick={() => save.mutate()}
      >
        {save.isPending ? "Kaydediliyor" : "Kaydet"}
      </button>
    </Modal>
  );
}
