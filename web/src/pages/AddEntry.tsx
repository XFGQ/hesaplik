import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import { money, parseNumber, toLocalInput } from "../lib/format";

type Kind = "DEBIT" | "CREDIT";

const BIRIMLER = ["balya", "kilo", "adet", "ton", "çuval", "litre"];

export default function AddEntry() {
  const { id } = useParams();
  const personId = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();

  const [kind, setKind] = useState<Kind>("DEBIT");
  const [when, setWhen] = useState(() => toLocalInput(new Date()));
  const [product, setProduct] = useState("");
  const [qty, setQty] = useState("");
  const [unit, setUnit] = useState("balya");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");

  const products = useQuery({ queryKey: ["products"], queryFn: api.products });

  function onProductChange(value: string) {
    setProduct(value);
    const key = value.trim().toLocaleLowerCase("tr");
    const hit = products.data?.find((p) => p.name.toLocaleLowerCase("tr") === key);
    if (hit) setUnit(hit.base_unit);
  }

  const save = useMutation({
    mutationFn: async () => {
      const occurred_at = new Date(when).toISOString();
      const hasItem = product.trim() && qty.trim();
      if (kind === "CREDIT") {
        return api.addPayment({
          person_id: personId,
          amount: parseNumber(amount),
          product_name: hasItem ? product.trim() : null,
          qty: hasItem ? parseNumber(qty) : null,
          unit: hasItem ? unit.trim() : null,
          occurred_at,
          note: note.trim() || null,
        });
      }
      return api.addDebt({
        person_id: personId,
        product_name: product.trim(),
        qty: parseNumber(qty),
        unit: unit.trim() || null,
        amount: parseNumber(amount),
        occurred_at,
        note: note.trim() || null,
      });
    },
    onSuccess: () => {
      ["balance", "transactions", "persons", "products"].forEach((k) =>
        qc.invalidateQueries({ queryKey: k === "products" ? ["products"] : [k, personId] }),
      );
      qc.invalidateQueries({ queryKey: ["persons"] });
      nav(`/kisi/${personId}`);
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

  const valid =
    tutar > 0 &&
    (kind === "CREDIT"
      ? product.trim().length === 0 || adet > 0
      : product.trim().length > 0 && adet > 0);

  return (
    <>
      <div className="bar">
        <button className="back" onClick={() => nav(`/kisi/${personId}`)}>
          ← Vazgeç
        </button>
        <h1>Kayıt ekle</h1>
      </div>

      <div className="pad">
        {/* Tarih en üstte, göz önünde ama küçük */}
        <label className="field" style={{ marginBottom: 16 }}>
          <span>Tarih ve saat</span>
          <input
            type="datetime-local"
            value={when}
            onChange={(e) => setWhen(e.target.value)}
          />
        </label>

        <div className="seg" role="group" aria-label="Kayıt türü">
          <button aria-pressed={kind === "DEBIT"} onClick={() => setKind("DEBIT")}>
            Borç
          </button>
          <button aria-pressed={kind === "CREDIT"} onClick={() => setKind("CREDIT")}>
            Tahsilat
          </button>
        </div>

        <div className="panel">
          <p className="panel-title">
            {kind === "DEBIT" ? "Ne verildi" : "Ürün (isteğe bağlı)"}
          </p>

          <label className="field">
            <span>Ürün</span>
            <input
              list="urunler"
              value={product}
              onChange={(e) => onProductChange(e.target.value)}
              placeholder={kind === "DEBIT" ? "saman" : "boş bırakırsan düz para sayılır"}
              autoFocus={kind === "DEBIT"}
            />
            <datalist id="urunler">
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
          <p className="panel-title">{kind === "DEBIT" ? "Borç tutarı" : "Alınan para"}</p>
          <label className="field">
            <span>Tutar (TL)</span>
            <input
              className="amount"
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder={kind === "DEBIT" ? "1500" : "2000"}
              autoFocus={kind === "CREDIT"}
            />
            {birimFiyat !== null && (
              <p className="hint">
                Birim fiyat {money(birimFiyat)}
                {unit.trim() ? ` / ${unit.trim()}` : ""}
              </p>
            )}
          </label>
          <label className="field">
            <span>Not (isteğe bağlı)</span>
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Örnek: kamyonla gönderildi"
            />
          </label>
        </div>

        <div className="total">
          <span className="muted">{kind === "DEBIT" ? "Borç" : "Tahsilat"}</span>
          <strong className={kind === "DEBIT" ? "borc" : "tahsilat"}>{money(tutar)}</strong>
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
      </div>
    </>
  );
}
