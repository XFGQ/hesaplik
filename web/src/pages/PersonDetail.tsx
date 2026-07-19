import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { TxDetail } from "../api/types";
import DebtModal from "../components/DebtModal";
import EditTxModal from "../components/EditTxModal";
import PaymentModal from "../components/PaymentModal";
import PersonModal from "../components/PersonModal";
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

export default function PersonDetail() {
  const { id } = useParams();
  const personId = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [modal, setModal] = useState<ModalKind>(null);
  const [editingTx, setEditingTx] = useState<TxDetail | null>(null);

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
    },
  });

  const remove = useMutation({
    mutationFn: () => api.deletePerson(personId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["persons"] });
      nav("/");
    },
  });

  function handleDelete(txId: number) {
    if (window.confirm("Bu kayıt silinsin mi? (arşive taşınır, canlı defterden çıkar)")) {
      del.mutate(txId);
    }
  }

  function deletePerson() {
    if (
      window.confirm(
        `${person.data?.full_name} defterden kaldırılsın mı? Geçmiş kayıtlar silinmez.`,
      )
    ) {
      remove.mutate();
    }
  }

  const tone = balance.data ? balanceTone(balance.data.balance_try) : "zero";
  const kapali = balance.data ? Number(balance.data.balance_try) === 0 : false;
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

      {del.isError && <p className="error">{(del.error as Error).message}</p>}
      {remove.isError && <p className="error">{(remove.error as Error).message}</p>}

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
                      <RowMenu onEdit={() => setEditingTx(t)} onDelete={() => handleDelete(t.id)} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="pad">
        <button className="danger" disabled={!kapali || remove.isPending} onClick={deletePerson}>
          {kapali ? "Kişiyi defterden kaldır" : "Hesap kapanınca kaldırılabilir"}
        </button>
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
    </div>
  );
}

function RowMenu({ onEdit, onDelete }: { onEdit: () => void; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <div className="row-menu" ref={ref}>
      <button
        className="row-menu-trigger"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label="İşlemler"
      >
        ⋮
      </button>
      {open && (
        <div className="row-menu-dropdown">
          <button
            onClick={() => {
              setOpen(false);
              onEdit();
            }}
          >
            Düzelt
          </button>
          <button
            className="row-menu-danger"
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
          >
            Sil
          </button>
        </div>
      )}
    </div>
  );
}
