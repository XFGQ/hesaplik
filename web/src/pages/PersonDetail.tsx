import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { TxDetail } from "../api/types";
import ConfirmDeleteModal from "../components/ConfirmDeleteModal";
import DebtModal from "../components/DebtModal";
import EditTxModal from "../components/EditTxModal";
import PaymentModal from "../components/PaymentModal";
import PersonModal from "../components/PersonModal";
import RowMenu from "../components/RowMenu";
import {
  balanceLabel,
  balanceTone,
  itemLabel,
  itemsSummary,
  money,
  qty,
  shortDate,
  signedMoney,
} from "../lib/format";
import { useToast } from "../lib/toast";

type ModalKind = "debt" | "payment" | "edit" | null;

function txSummary(t: TxDetail): string {
  const isDebt = t.kind === "DEBIT";
  const parts = [shortDate(t.occurred_at)];
  if (t.lines.length > 0) {
    const l = t.lines[0];
    parts.push(`${qty(l.qty)} ${l.unit} ${l.product_name.toLocaleLowerCase("tr")}`);
  } else if (t.note) {
    parts.push(t.note);
  } else {
    parts.push(isDebt ? "borç" : "tahsilat");
  }
  parts.push(`${isDebt ? "+" : "−"}${money(t.amount_try)}`);
  return parts.join(" · ");
}

export default function PersonDetail() {
  const { id } = useParams();
  const personId = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [modal, setModal] = useState<ModalKind>(null);
  const [editingTx, setEditingTx] = useState<TxDetail | null>(null);
  const [deletingTx, setDeletingTx] = useState<TxDetail | null>(null);

  const person = useQuery({
    queryKey: ["person", personId],
    queryFn: () => api.person(personId),
  });
  const balance = useQuery({
    queryKey: ["balance", personId],
    queryFn: () => api.balance(personId),
  });
  const txs = useQuery({
    queryKey: ["transactions", personId],
    queryFn: () => api.transactions(personId),
  });

  function refresh() {
    qc.invalidateQueries({ queryKey: ["balance", personId] });
    qc.invalidateQueries({ queryKey: ["transactions", personId] });
    qc.invalidateQueries({ queryKey: ["persons"] });
  }

  const del = useMutation({
    mutationFn: (txId: number) => api.deleteTransaction(txId, "kullanıcı sildi"),
    onSuccess: () => {
      refresh();
      toast("Kayıt silindi", "success");
      setDeletingTx(null);
    },
  });

  const tone = balance.data ? balanceTone(balance.data.balance_try) : "zero";
  const p = person.data;

  return (
    <div className="page">
      <div className="bar">
        <button className="back" onClick={() => nav("/")}>
          ← Defter
        </button>
        <h1 style={{ cursor: "pointer" }} onClick={() => setModal("edit")}>
          {p?.full_name ?? "Kişi"}
        </h1>
        <button className="link" onClick={() => setModal("edit")}>
          Düzenle
        </button>
        <button className="link" onClick={() => window.open(api.personReportUrl(personId), "_blank")}>
          <img className="report-icon" src="/icons/pdf_logo.svg" alt="" /> Ekstre (PDF)
        </button>
      </div>

      <div className="balance">
        <div className="balance-label">
          {balance.data ? balanceLabel(balance.data.balance_try) : "hesaplanıyor"}
        </div>
        <div className={`balance-amount ${tone}`}>
          {balance.data ? signedMoney(balance.data.balance_try) : "—"}
        </div>
        {balance.data && balance.data.items.length > 0 && (
          <div className="chips">
            {balance.data.items.map((it) => (
              <span
                className={`chip ${Number(it.qty) < 0 ? "chip-owe" : ""}`}
                key={`${it.product_name}-${it.unit}`}
              >
                {itemLabel(it)}
              </span>
            ))}
          </div>
        )}
      </div>

      {p && (p.phone || p.district || p.city || p.address || p.note) && (
        <div className="meta">
          {p.phone && <span>{p.phone}</span>}
          {(p.district || p.city) && (
            <span>{[p.district, p.city].filter(Boolean).join(" / ")}</span>
          )}
          {p.address && <span>{p.address}</span>}
          {p.note && <span style={{ width: "100%" }}>Not: {p.note}</span>}
        </div>
      )}

      <div className="tx-area">
        {txs.data?.length === 0 && (
          <div className="empty">
            <p>Hareket yok.</p>
            <p className="muted">Aşağıdan borç veya tahsilat ekle.</p>
          </div>
        )}

        {txs.data && txs.data.length > 0 && (
          <div className="tx-table-wrap">
            <table>
            <thead>
              <tr>
                <th>Tarih</th>
                <th>Ürün</th>
                <th style={{ textAlign: "right" }}>Adet</th>
                <th style={{ textAlign: "right" }}>Birim fiyat</th>
                <th style={{ textAlign: "right" }}>Tutar</th>
                <th aria-hidden="true"></th>
              </tr>
            </thead>
            <tbody>
              {txs.data.map((t) => {
                const isDebt = t.kind === "DEBIT";
                const hasLines = t.lines.length > 0;
                const detail = hasLines
                  ? itemsSummary(t.lines)
                  : (t.note ?? (isDebt ? "borç" : "tahsilat"));
                return (
                  <tr key={t.id} className={t.is_reversed ? "struck" : undefined}>
                    <td className="muted">{shortDate(t.occurred_at)}</td>
                    <td>
                      {detail}
                      {t.reverses_id ? " · iptal kaydı" : ""}
                      {t.is_reversed ? " · iptal edildi" : ""}
                    </td>
                    <td className="num">{hasLines ? qty(t.lines[0].qty) : "—"}</td>
                    <td className="num">
                      {hasLines ? `${money(t.lines[0].unit_price)}/${t.lines[0].unit}` : "—"}
                    </td>
                    <td className={`num ${isDebt ? "borc" : "tahsilat"}`}>
                      {isDebt ? "+" : "−"}
                      {money(t.amount_try)}
                    </td>
                    <td className="row-menu-cell">
                      <RowMenu
                        items={[
                          { label: "Düzelt", onClick: () => setEditingTx(t) },
                          { label: "Sil", onClick: () => setDeletingTx(t), danger: true },
                        ]}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </div>

      <div className="fab-row">
        <button className="fab-borc" onClick={() => setModal("debt")}>
          Borç ekle
        </button>
        <button className="fab-tahsilat" onClick={() => setModal("payment")}>
          Tahsilat ekle
        </button>
      </div>

      {modal === "debt" && p && (
        <DebtModal personId={personId} personName={p.full_name} onClose={() => setModal(null)} />
      )}
      {modal === "payment" && p && (
        <PaymentModal
          personId={personId}
          personName={p.full_name}
          onClose={() => setModal(null)}
        />
      )}
      {modal === "edit" && p && <PersonModal person={p} onClose={() => setModal(null)} />}
      {editingTx && (
        <EditTxModal tx={editingTx} personId={personId} onClose={() => setEditingTx(null)} />
      )}
      {deletingTx && (
        <ConfirmDeleteModal
          title="Kaydı sil"
          description={
            <>
              <p className="panel-title">Silinecek kayıt</p>
              <p style={{ margin: 0 }}>{txSummary(deletingTx)}</p>
            </>
          }
          warning="Bu kayıt defterden kalkacak ve bakiye yeniden hesaplanacak."
          onConfirm={() => del.mutate(deletingTx.id)}
          onClose={() => setDeletingTx(null)}
          isPending={del.isPending}
          error={del.isError ? (del.error as Error).message : null}
        />
      )}
    </div>
  );
}
