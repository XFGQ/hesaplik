import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import { money, parseNumber, toLocalInput } from "../lib/format";
import {
  BIRIMLER,
  autoAmount,
  checkForm,
  goodsSummary,
  resolveGoods,
} from "../lib/goods";
import { runningDirectionWarning } from "../lib/running";
import { useToast } from "../lib/toast";
import Modal from "./Modal";

type Props = {
  personId: number;
  personName: string;
  onClose: () => void;
};

export default function PaymentModal({ personId, personName, onClose }: Props) {
  const qc = useQueryClient();
  const toast = useToast();

  const [when, setWhen] = useState(() => toLocalInput(new Date()));
  // Borçtaki ile AYNI akıllı alan; tek farkı boş bırakılabilmesi (düz para).
  const [entry, setEntry] = useState("");
  const [unitFallback, setUnitFallback] = useState<string | null>(null);
  const [unitOpen, setUnitOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [amountHint, setAmountHint] = useState<number | null>(null);
  // Tik varsayılan olarak KAPALI (CLAUDE.md kural 4: fiyat listesi bağlamaz).
  const [autoPrice, setAutoPrice] = useState(false);

  const products = useQuery({ queryKey: ["products"], queryFn: api.products });

  const goods = resolveGoods(entry, products.data, unitFallback);
  const otomatik = autoAmount(goods);
  const otomatikAcik = autoPrice && otomatik !== null;

  function onEntryChange(value: string) {
    setEntry(value);
    const next = resolveGoods(value, products.data, unitFallback);
    if (next.unit) setUnitFallback(null);
    if (next.amountHint !== null && next.amountHint !== amountHint) {
      const oncekiOneri = amountHint === null ? null : String(amountHint).replace(".", ",");
      if (!amount.trim() || amount === oncekiOneri) {
        setAmount(String(next.amountHint).replace(".", ","));
        setAutoPrice(false);
      }
    }
    setAmountHint(next.amountHint);
  }

  const tutar = otomatikAcik ? otomatik : Number(parseNumber(amount)) || 0;
  const birimFiyat = goods.qty && goods.qty > 0 && tutar > 0 ? tutar / goods.qty : null;
  const yonUyari = goods.running ? runningDirectionWarning(goods.running, "payment") : null;
  const check = checkForm(goods, otomatikAcik ? String(otomatik) : amount, {
    goodsRequired: false,
  });

  const save = useMutation({
    mutationFn: () =>
      api.addPayment({
        person_id: personId,
        amount: tutar.toFixed(2),
        // Alan boşsa kalem yok: düz para tahsilatı.
        product_name: goods.empty ? null : goods.productName,
        qty: goods.empty ? null : String(goods.qty),
        unit: goods.empty ? null : goods.unitName,
        occurred_at: new Date(when).toISOString(),
      }),
    onSuccess: () => {
      ["balance", "transactions"].forEach((k) =>
        qc.invalidateQueries({ queryKey: [k, personId] }),
      );
      qc.invalidateQueries({ queryKey: ["persons"] });
      qc.invalidateQueries({ queryKey: ["products"] });
      toast(`${personName}'ndan ${money(tutar)} tahsilat eklendi`, "success");
      onClose();
    },
  });

  return (
    <Modal title="Tahsilat ekle" onClose={onClose}>
      <label className="field" style={{ marginBottom: 16 }}>
        <span>Tarih ve saat</span>
        <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
      </label>

      <div className="panel">
        <p className="panel-title">Ürün (isteğe bağlı)</p>

        <label className="field">
          <span>Ürün ve adet</span>
          <input
            value={entry}
            onChange={(e) => onEntryChange(e.target.value)}
            placeholder="boş bırakırsan düz para — örn: 20 kg arpa · 70-30-100"
          />
        </label>

        {goods.error ? (
          <p className="error">{goods.error}</p>
        ) : (
          goods.qty !== null && (
            <p className="goods-echo">
              <span aria-hidden>→</span>
              <b>{goodsSummary(goods)}</b>
            </p>
          )
        )}
        {goods.empty && products.data && products.data.length > 0 && (
          // Datalist yerine düz ipucu: kullanıcı serbest yazıyor, açılır liste
          // "20 kg ar" gibi yarım cümlede zaten eşleşmiyordu.
          <p className="hint">
            Kayıtlı ürünler: {products.data.map((p) => p.name.toLocaleLowerCase("tr")).join(", ")}
          </p>
        )}
        {!goods.error && goods.isNewProduct && (
          <p className="hint">Yeni ürün açılacak: {goods.productName}</p>
        )}
        {!goods.error && yonUyari && <p className="hint">{yonUyari}</p>}

        {!goods.empty &&
          (unitOpen ? (
            <div className="goods-unit">
              <select
                value={goods.unitName}
                onChange={(e) => setUnitFallback(e.target.value)}
                aria-label="Birim"
              >
                {[...new Set([goods.unitName, ...BIRIMLER])].map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
              <button className="link" type="button" onClick={() => setUnitOpen(false)}>
                Kapat
              </button>
            </div>
          ) : (
            <button className="link" type="button" onClick={() => setUnitOpen(true)}>
              Birim: {goods.unitName} — değiştir
            </button>
          ))}
      </div>

      <div className="panel">
        <p className="panel-title">Alınan para</p>

        {!goods.empty && (
          <label className={otomatik === null ? "check off" : "check"}>
            <input
              type="checkbox"
              checked={otomatikAcik}
              disabled={otomatik === null}
              onChange={(e) => setAutoPrice(e.target.checked)}
            />
            <span>
              {goods.catalogPrice !== null
                ? `Kayıtlı fiyattan hesapla (${money(goods.catalogPrice)}/${goods.unitName})`
                : goods.priceUnit
                  ? `Kayıtlı fiyat ${goods.priceUnit} için — ${goods.unitName} fiyatı elle girilir`
                  : "Kayıtlı fiyat yok — tutarı elle girin"}
            </span>
          </label>
        )}

        <label className="field">
          <span>Tutar (TL)</span>
          <input
            className="amount"
            inputMode="decimal"
            value={otomatikAcik ? String(otomatik).replace(".", ",") : amount}
            readOnly={otomatikAcik}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="2000"
            autoFocus
          />
          {birimFiyat !== null && (
            <p className="hint">
              Birim fiyat {money(birimFiyat)} / {goods.unitName}
            </p>
          )}
        </label>
      </div>

      <div className="total">
        <span className="muted">Tahsilat</span>
        <strong className="tahsilat">{money(tutar)}</strong>
      </div>

      {save.isError && (
        <p className="error" style={{ margin: "0 0 12px" }}>
          {(save.error as Error).message}
        </p>
      )}

      <button
        className="primary"
        disabled={!check.ok || save.isPending}
        onClick={() => save.mutate()}
      >
        {save.isPending ? "Kaydediliyor" : check.ok ? "Kaydet" : (check.reason ?? "Kaydet")}
      </button>
    </Modal>
  );
}
