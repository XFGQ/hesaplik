import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import { money, parseNumber, toLocalInput } from "../lib/format";
import {
  BIRIMLER,
  autoAmount,
  checkForm,
  defaultAutoPrice,
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

export default function DebtModal({ personId, personName, onClose }: Props) {
  const qc = useQueryClient();
  const toast = useToast();

  const [when, setWhen] = useState(() => toLocalInput(new Date()));
  // Ürün + adet + birim TEK alanda ("20", "20 kg arpa", "70-20-50").
  // Ayrıştırma web/src/lib/goods.ts'te, DOM'suz test edilir.
  const [entry, setEntry] = useState("");
  const [unitFallback, setUnitFallback] = useState<string | null>(null);
  const [unitOpen, setUnitOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [amountHint, setAmountHint] = useState<number | null>(null);
  // null = kullanıcı tike dokunmadı. O zaman tik yalnızca varsayılan saman
  // fiyatında AÇIK başlar (tutar yazılmadıysa saman bu fiyattan hesaplanır —
  // CLAUDE.md > "Varsayılan saman fiyatı"); diğer ürünlerde kayıtlı fiyat
  // tutarı bağlamaz (kural 4), kapalı başlar.
  const [autoPrice, setAutoPrice] = useState<boolean | null>(null);

  const products = useQuery({ queryKey: ["products"], queryFn: api.products });
  const samanFiyat = useQuery({ queryKey: ["saman-fiyat"], queryFn: api.samanFiyat });
  const samanPrice =
    samanFiyat.data?.unit_price != null ? Number(samanFiyat.data.unit_price) : null;

  const goods = resolveGoods(entry, products.data, unitFallback, samanPrice);
  const otomatik = autoAmount(goods);
  // Kullanıcı tutarı elle yazdıysa varsayılan fiyat onu ezmez.
  const tikAcik = autoPrice ?? (defaultAutoPrice(goods) && !amount.trim());
  const otomatikAcik = tikAcik && otomatik !== null;

  function onEntryChange(value: string) {
    setEntry(value);
    const next = resolveGoods(value, products.data, unitFallback, samanPrice);
    // Yazıda birim varsa dropdown yedeği düşer: yazı her zaman kazanır.
    if (next.unit) setUnitFallback(null);
    // "... 5000 tl" aynı satıra yazıldıysa tutarı doldur — ama kullanıcının
    // elle yazdığı tutarı EZMEZ (sessiz para değişikliği olmaz).
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
  const yonUyari = goods.running ? runningDirectionWarning(goods.running, "debt") : null;
  const check = checkForm(goods, otomatikAcik ? String(otomatik) : amount, {
    goodsRequired: true,
  });

  const save = useMutation({
    mutationFn: () =>
      api.addDebt({
        person_id: personId,
        product_name: goods.productName,
        qty: String(goods.qty),
        unit: goods.unitName,
        amount: tutar.toFixed(2),
        occurred_at: new Date(when).toISOString(),
      }),
    onSuccess: () => {
      ["balance", "transactions"].forEach((k) =>
        qc.invalidateQueries({ queryKey: [k, personId] }),
      );
      qc.invalidateQueries({ queryKey: ["persons"] });
      qc.invalidateQueries({ queryKey: ["products"] });
      toast(
        `${personName}'na ${goods.qty} ${goods.unitName} ${goods.productName} borç eklendi`,
        "success",
      );
      onClose();
    },
  });

  return (
    <Modal title="Borç ekle" onClose={onClose}>
      <label className="field" style={{ marginBottom: 16 }}>
        <span>Tarih ve saat</span>
        <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
      </label>

      <div className="panel">
        <p className="panel-title">Ne verildi</p>

        <label className="field">
          <span>Ürün ve adet</span>
          <input
            value={entry}
            onChange={(e) => onEntryChange(e.target.value)}
            placeholder="örn: 20 · 20 kg arpa · 70-20-50"
            autoFocus
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

        {unitOpen ? (
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
        )}
      </div>

      <div className="panel">
        <p className="panel-title">Borç tutarı</p>

        {/* Fiyat zorunlu; kayıtlı birim fiyat varsa tek tıkla hesaplanır.
            Fiyat yoksa ya da birim ürünün birimiyle tutmuyorsa tik pasif —
            balya fiyatı kilo adediyle çarpılmaz. */}
        <label className={otomatik === null ? "check off" : "check"}>
          <input
            type="checkbox"
            checked={otomatikAcik}
            disabled={otomatik === null}
            onChange={(e) => setAutoPrice(e.target.checked)}
          />
          <span>
            {goods.priceSource === "saman" && goods.catalogPrice !== null
              ? `Varsayılan saman fiyatından hesapla (${money(goods.catalogPrice)}/${goods.unitName})`
              : goods.catalogPrice !== null
              ? `Kayıtlı fiyattan hesapla (${money(goods.catalogPrice)}/${goods.unitName})`
              : goods.priceUnit
                ? `Kayıtlı fiyat ${goods.priceUnit} için — ${goods.unitName} fiyatı elle girilir`
                : "Kayıtlı fiyat yok — tutarı elle girin"}
          </span>
        </label>

        <label className="field">
          <span>Tutar (TL)</span>
          <input
            className="amount"
            inputMode="decimal"
            value={otomatikAcik ? String(otomatik).replace(".", ",") : amount}
            readOnly={otomatikAcik}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="1500"
          />
          {birimFiyat !== null && (
            <p className="hint">
              Birim fiyat {money(birimFiyat)} / {goods.unitName}
            </p>
          )}
        </label>
      </div>

      <div className="total">
        <span className="muted">Borç</span>
        <strong className="borc">{money(tutar)}</strong>
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
