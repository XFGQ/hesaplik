import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { hhmm, shortDate } from "../lib/format";
import { useToast } from "../lib/toast";
import Modal from "./Modal";

type Props = {
  onClose: () => void;
};

function backupSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

export default function SettingsModal({ onClose }: Props) {
  const qc = useQueryClient();
  const toast = useToast();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.getSettings });
  const backups = useQuery({ queryKey: ["backups"], queryFn: api.getBackups, retry: false });
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

  const runBackup = useMutation({
    mutationFn: api.runBackup,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["backups"] });
      toast("Yedek alındı", "success");
    },
  });

  const valid = businessName.trim().length >= 2;

  const snapshots = [...(backups.data ?? [])].sort((a, b) => b.time.localeCompare(a.time));
  const last = snapshots[0];

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
        {backups.isLoading && <p className="muted">Yükleniyor…</p>}
        {backups.isError && <p className="muted">Yedekleme yapılandırılmamış</p>}
        {!backups.isLoading && !backups.isError && (
          <>
            <p className="muted">
              {last
                ? `Son yedek: ${shortDate(last.time)} · ${hhmm(new Date(last.time))} · ${backupSize(last.size_bytes)}`
                : "Henüz yedek alınmadı"}
            </p>
            <p className="muted">Toplam yedek: {snapshots.length}</p>
            <button
              type="button"
              className="link"
              disabled={runBackup.isPending}
              onClick={() => runBackup.mutate()}
            >
              {runBackup.isPending ? "Alınıyor…" : "Şimdi yedekle"}
            </button>
            {runBackup.isError && (
              <p className="error">{(runBackup.error as Error).message}</p>
            )}
          </>
        )}
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
