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
import { ActionBarMain } from "../lib/actionBar";
import {
  accountTone,
  balanceLabel,
  dateParts,
  itemLabel,
  money,
  shortDate,
  signedMoney,
  txCardTitle,
  txKindLabel,
  txUnitPrice,
} from "../lib/format";
import { useToast } from "../lib/toast";

type ModalKind = "debt" | "payment" | "edit" | null;

/** Silme onayındaki tek satırlık özet — karttakiyle aynı dili konuşur. */
function txSummary(t: TxDetail): string {
  const isDebt = t.kind === "DEBIT";
  return [
    shortDate(t.occurred_at),
    txCardTitle(t),
    `${isDebt ? "+" : "−"}${money(t.amount_try)}`,
  ].join(" · ");
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

  const tone = balance.data ? accountTone(balance.data.balance_try) : "zero";
  const p = person.data;

  return (
    <div className="page">
      <div className="bar bar-person">
        {/* Sol sütun: [← Defter] üstte, [Ekstre (PDF)] altında. Geri tuşu
            çerçeveli ve büyük (min 52px dokunma alanı) — eskiden kenarlıksız
            küçük bir yazıydı ve hemen yanındaki tıklanabilir kişi adına
            yanlışlıkla basılıyordu. Ekstre alt satırda kendi tam boy (--tap)
            hedefi; ikisi aynı genişlikte, aralarında parmak payı var. */}
        <div className="bar-nav">
          <button className="back" onClick={() => nav("/")} aria-label="Deftere dön">
            <span className="back-arrow" aria-hidden="true">
              ←
            </span>
            Defter
          </button>
          <button
            className="link"
            onClick={() => api.openPersonReport(personId).catch(() => toast("Rapor alınamadı", "info"))}
          >
            <img className="report-icon" src="/icons/pdf_logo.svg" alt="" /> Ekstre (PDF)
          </button>
        </div>
        <h1 style={{ cursor: "pointer" }} onClick={() => setModal("edit")}>
          {p?.full_name ?? "Kişi"}
        </h1>
        {/* Sağ üstte yalnızca Düzenle. */}
        <div className="bar-actions">
          <button className="link" onClick={() => setModal("edit")}>
            Düzenle
          </button>
        </div>
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
          <ol className="tx-cards">
            {txs.data.map((t) => {
              const isDebt = t.kind === "DEBIT";
              const gun = dateParts(t.occurred_at);
              const birim = txUnitPrice(t);
              return (
                <li
                  key={t.id}
                  className={`tx-card ${isDebt ? "tx-card-borc" : "tx-card-tahsilat"}${
                    t.is_reversed || t.reverses_id ? " tx-card-iptal" : ""
                  }`}
                >
                  {/* Dikey tarih: gün büyük, ay-yıl küçük, saat en altta ve
                      en soluk. Defterde gözün ilk aradığı şey "ne zaman" — o
                      yüzden en solda, tek başına. Saat ikincil bilgi: aynı gün
                      birden çok kayıt varsa sırayı ayırt eder, gün/ay okumayı
                      zorlaştırmaz. */}
                  <div className="tx-date">
                    <span className="tx-day">{gun.day}</span>
                    <span className="tx-month">{gun.monthYear}</span>
                    <span className="tx-time">{gun.time}</span>
                  </div>

                  <div className="tx-body">
                    <div className="tx-line">
                      <span className="tx-title">{txCardTitle(t)}</span>
                      <span className={`tx-amount ${isDebt ? "borc" : "tahsilat"}`}>
                        {isDebt ? "+" : "−"}
                        {money(t.amount_try)}
                      </span>
                    </div>
                    <div className="tx-line tx-line-sub">
                      <span className="tx-kind">
                        {txKindLabel(t)}
                        {birim && <span className="tx-birim"> · {birim}</span>}
                      </span>
                      {/* Koşan bakiye: bu kayıt işlendikten SONRAKİ toplam
                          (sunucudan gelir). Onaysız kayıt bakiyeye girmez,
                          orada gösterilecek bir sayı da yoktur. */}
                      {t.running_balance_try !== null && (
                        <span className="tx-running">
                          Bakiye{" "}
                          <b className={accountTone(t.running_balance_try)}>
                            {signedMoney(t.running_balance_try)}
                          </b>
                        </span>
                      )}
                    </div>
                  </div>

                  <RowMenu
                    items={[
                      { label: "Düzelt", onClick: () => setEditingTx(t) },
                      { label: "Sil", onClick: () => setDeletingTx(t), danger: true },
                    ]}
                  />
                </li>
              );
            })}
          </ol>
        )}
      </div>

      <ActionBarMain>
        <div className="fab-row">
          <button className="fab-borc" onClick={() => setModal("debt")}>
            Borç ekle
          </button>
          <button className="fab-tahsilat" onClick={() => setModal("payment")}>
            Tahsilat ekle
          </button>
        </div>
      </ActionBarMain>

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
