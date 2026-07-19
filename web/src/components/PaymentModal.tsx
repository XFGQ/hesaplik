import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import { money, parseNumber, toLocalInput } from "../lib/format";
import { useToast } from "../lib/toast";
import Modal from "./Modal";

const BIRIMLER = ["balya", "kilo", "adet", "ton", "çuval", "litre"];

type Props = {
  personId: number;
  personName: string;
  onClose: () => void;
};

export default function PaymentModal({ personId, personName, onClose }: Props) {
  const qc = useQueryClient();
  const toast = useToast();

  const [when, setWhen] = useState(() => toLocalInput(new Date()));
  const [product, setProduct] = useState("");
  const [qty, setQty] = useState("");
  const [unit, setUnit] = useState("balya");
  const [amount, setAmount] = useState("");

  const products = useQuery({ queryKey: ["products"], queryFn: api.products });

  function onProductChange(value: string) {
    setProduct(value);
    const key = value.trim().toLocaleLowerCase("tr");
    const hit = products.data?.find((p) => p.name.toLocaleLowerCase("tr") === key);
    if (hit) setUnit(hit.base_unit);
  }

  const save = useMutation({
    mutationFn: () => {
      const hasItem = product.trim() && qty.trim();
      return api.addPayment({
        person_id: personId,
        amount: parseNumber(amount),
        product_name: hasItem ? product.trim() : null,
        qty: hasItem ? parseNumber(qty) : null,
        unit: hasItem ? unit.trim() : null,
        occurred_at: new Date(when).toISOString(),
      });
    },
    onSuccess: () => {
      ["balance", "transactions"].forEach((k) =>
        qc.invalidateQueries({ queryKey: [k, personId] }),
      );
      qc.invalidateQueries({ queryKey: ["persons"] });
      qc.invalidateQueries({ queryKey: ["products"] });
      toast(`${personName}'ndan ${money(amount)} tahsilat eklendi`, "success");
      onClose();
    },
  });

  const tutar = Number(parseNumber(amount)) || 0;
  const adet = Number(parseNumber(qty)) || 0;
  const birimFiyat = adet > 0 && tutar > 0 ? tutar / adet : null;

  const showQtyUnit = product.trim().length > 0;

  const yeniUrun =
    product.trim().length > 1 &&
    products.data !== undefined &&
    !products.data.some(
      (p) => p.name.toLocaleLowerCase("tr") === product.trim().toLocaleLowerCase("tr"),
    );

  const valid = tutar > 0 && (product.trim().length === 0 || adet > 0);

  return (
    <Modal title="Tahsilat ekle" onClose={onClose}>
      <label className="field" style={{ marginBottom: 16 }}>
        <span>Tarih ve saat</span>
        <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
      </label>

      <div className="panel">
        <p className="panel-title">Ürün (isteğe bağlı)</p>

        <label className="field">
          <span>Ürün</span>
          <input
            list="urunler-tahsilat"
            value={product}
            onChange={(e) => onProductChange(e.target.value)}
            placeholder="boş bırakırsan düz para sayılır"
          />
          <datalist id="urunler-tahsilat">
            {products.data?.map((p) => (
              <option key={p.id} value={p.name} />
            ))}
          </datalist>
          {yeniUrun && <p className="hint">Yeni ürün açılacak: {product.trim()}</p>}
        </label>

        {showQtyUnit && (
          <label className="field">
            <span>Adet ve birim</span>
            <div className="qty-unit">
              <input
                inputMode="decimal"
                value={qty}
                onChange={(e) => setQty(e.target.value)}
                placeholder="20"
              />
              <select value={unit} onChange={(e) => setUnit(e.target.value)}>
                {BIRIMLER.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
            </div>
          </label>
        )}
      </div>

      <div className="panel">
        <p className="panel-title">Alınan para</p>
        <label className="field">
          <span>Tutar (TL)</span>
          <input
            className="amount"
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="2000"
            autoFocus
          />
          {birimFiyat !== null && (
            <p className="hint">
              Birim fiyat {money(birimFiyat)}
              {unit.trim() ? ` / ${unit.trim()}` : ""}
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
        disabled={!valid || save.isPending}
        onClick={() => save.mutate()}
      >
        {save.isPending ? "Kaydediliyor" : "Kaydet"}
      </button>
    </Modal>
  );
}
