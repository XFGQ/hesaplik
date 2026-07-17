import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import { money } from "../lib/format";

type Kind = "DEBIT" | "CREDIT";

export default function AddEntry() {
  const { id } = useParams();
  const personId = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();

  const [kind, setKind] = useState<Kind>("DEBIT");
  const [productId, setProductId] = useState<number | "">("");
  const [amount, setAmount] = useState("");

  const products = useQuery({ queryKey: ["products"], queryFn: api.products });
  const product = products.data?.find((p) => p.id === productId);

  const total = useMemo(() => {
    if (kind === "CREDIT") return Number(amount.replace(",", ".")) || 0;
    if (!product?.unit_price) return 0;
    return (Number(amount.replace(",", ".")) || 0) * Number(product.unit_price);
  }, [kind, amount, product]);

  const save = useMutation({
    mutationFn: async () => {
      const value = amount.replace(",", ".");
      if (kind === "CREDIT") return api.addPayment(personId, value);
      if (productId === "") throw new Error("Ürün seç");
      return api.addDebt(personId, productId, value);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["balance", personId] });
      qc.invalidateQueries({ queryKey: ["transactions", personId] });
      qc.invalidateQueries({ queryKey: ["persons"] });
      nav(`/kisi/${personId}`);
    },
  });

  const valid =
    Number(amount.replace(",", ".")) > 0 && (kind === "CREDIT" || productId !== "");

  return (
    <>
      <div className="bar">
        <button className="back" onClick={() => nav(`/kisi/${personId}`)}>
          ← Vazgeç
        </button>
        <h1>Kayıt ekle</h1>
      </div>

      <div className="pad">
        <div className="seg" role="group" aria-label="Kayıt türü">
          <button aria-pressed={kind === "DEBIT"} onClick={() => setKind("DEBIT")}>
            Borç
          </button>
          <button aria-pressed={kind === "CREDIT"} onClick={() => setKind("CREDIT")}>
            Tahsilat
          </button>
        </div>

        <div style={{ height: 18 }} />

        {kind === "DEBIT" && (
          <label className="field">
            <span>Ürün</span>
            <select
              value={productId}
              onChange={(e) => setProductId(e.target.value ? Number(e.target.value) : "")}
            >
              <option value="">Seç</option>
              {products.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} — {p.unit_price ? `${money(p.unit_price)}/${p.base_unit}` : "fiyat yok"}
                </option>
              ))}
            </select>
          </label>
        )}

        <label className="field">
          <span>{kind === "DEBIT" ? `Adet${product ? ` (${product.base_unit})` : ""}` : "Tutar (TL)"}</span>
          <input
            type="text"
            inputMode="decimal"
            placeholder={kind === "DEBIT" ? "20" : "2000"}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>

        <div className="total">
          <span className="muted">{kind === "DEBIT" ? "Borç tutarı" : "Tahsilat"}</span>
          <strong className={kind === "DEBIT" ? "borc" : "tahsilat"}>{money(total)}</strong>
        </div>

        {kind === "DEBIT" && product && !product.unit_price && (
          <p className="error" style={{ margin: "0 0 12px" }}>
            Bu ürünün fiyatı tanımlı değil. Kayıt açılamaz.
          </p>
        )}

        {save.isError && <p className="error" style={{ margin: "0 0 12px" }}>{(save.error as Error).message}</p>}

        <button className="primary" disabled={!valid || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Kaydediliyor" : "Kaydet"}
        </button>
      </div>
    </>
  );
}
