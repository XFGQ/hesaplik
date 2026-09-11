import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import { money, parseNumber, toEditableNumber } from "../lib/format";
import { useToast } from "../lib/toast";
import Modal from "./Modal";

type Props = {
  /** Sunucudaki güncel fiyat ("180.00"); ayar bozuksa null. */
  current: string | null;
  onClose: () => void;
};

/** Üst sınır sunucudakiyle aynı (saman_fiyat.SAMAN_FIYAT_MAX): fazladan
 *  yazılmış bir sıfır sessizce kabul edilmesin. */
const FIYAT_UST_SINIR = 1_000_000;

/* Varsayılan saman balya fiyatı (CLAUDE.md > "Varsayılan saman fiyatı").
 * Doğrulama ve audit sunucuda; burası yalnızca Kaydet'in açık olup
 * olmayacağına karar verir. */
export default function SamanFiyatModal({ current, onClose }: Props) {
  const qc = useQueryClient();
  const toast = useToast();
  const [value, setValue] = useState(() => (current ? toEditableNumber(current) : ""));

  const normal = parseNumber(value);
  const fiyat = Number(normal);
  const valid = normal !== "" && Number.isFinite(fiyat) && fiyat > 0 && fiyat <= FIYAT_UST_SINIR;

  const save = useMutation({
    mutationFn: () => api.setSamanFiyat(normal),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["saman-fiyat"] });
      toast(`Saman fiyatı ${money(res.unit_price ?? normal)} / ${res.unit} olarak güncellendi`, "success");
      onClose();
    },
  });

  return (
    <Modal title="Güncel saman fiyatı" onClose={onClose}>
      <label className="field">
        <span>Balya başına fiyat (TL)</span>
        <input
          className="amount"
          inputMode="decimal"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && valid && !save.isPending) save.mutate();
          }}
          placeholder="180"
          autoFocus
        />
      </label>
      <p className="hint">
        Samanda tutar yazılmazsa adet × bu fiyat hesaplanır. Tutar yazarsanız sizin tutarınız
        geçerlidir. Diğer ürünler etkilenmez.
      </p>

      {save.isError && (
        <p className="error" style={{ margin: "0 0 12px" }}>
          {(save.error as Error).message}
        </p>
      )}

      <button className="primary" disabled={!valid || save.isPending} onClick={() => save.mutate()}>
        {save.isPending ? "Kaydediliyor" : valid ? "Kaydet" : "Fiyat girin"}
      </button>
    </Modal>
  );
}
