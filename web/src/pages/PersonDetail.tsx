import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import { balanceLabel, balanceTone, itemLabel, itemsSummary, money, shortDate, signedMoney } from "../lib/format";

export default function PersonDetail() {
  const { id } = useParams();
  const personId = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();

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

  const reverse = useMutation({
    mutationFn: ({ txId, reason }: { txId: number; reason: string }) =>
      api.reverse(txId, reason),
    onSuccess: refresh,
  });

  const remove = useMutation({
    mutationFn: () => api.deletePerson(personId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["persons"] });
      nav("/");
    },
  });

  function cancel(txId: number) {
    const reason = window.prompt("İptal sebebi (kayıt silinmez, ters kayıt açılır)");
    if (reason && reason.trim().length >= 3) reverse.mutate({ txId, reason: reason.trim() });
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
    <>
      <div className="bar">
        <button className="back" onClick={() => nav("/")}>
          ← Defter
        </button>
        <h1>{p?.full_name ?? "Kişi"}</h1>
        <button className="link" onClick={() => nav(`/kisi/${personId}/duzenle`)}>
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

      {reverse.isError && <p className="error">{(reverse.error as Error).message}</p>}
      {remove.isError && <p className="error">{(remove.error as Error).message}</p>}

      {txs.data?.length === 0 && (
        <div className="empty">
          <p>Hareket yok.</p>
          <p className="muted">Aşağıdan borç veya tahsilat ekle.</p>
        </div>
      )}

      <ul className="ledger">
        {txs.data?.map((t) => {
          const isDebt = t.kind === "DEBIT";
          const detail = t.lines.length
            ? itemsSummary(t.lines)
            : (t.note ?? (isDebt ? "borç" : "tahsilat"));
          return (
            <li key={t.id} className={t.is_reversed ? "struck" : undefined}>
              <div className="row" style={{ display: "block" }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                  <span className="row-name">{detail}</span>
                  <span className="row-leader" aria-hidden="true" />
                  <span className={`row-amount ${isDebt ? "borc" : "tahsilat"}`}>
                    {isDebt ? "+" : "−"}
                    {money(t.amount_try)}
                  </span>
                </div>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <span className="muted">
                    {shortDate(t.occurred_at)}
                    {t.reverses_id ? " · iptal kaydı" : ""}
                    {t.is_reversed ? " · iptal edildi" : ""}
                    {t.lines.length > 0 && !t.is_reversed
                      ? ` · ${money(t.lines[0].unit_price)}/${t.lines[0].unit}`
                      : ""}
                  </span>
                  {!t.is_reversed && !t.reverses_id && (
                    <button className="ghost" onClick={() => cancel(t.id)}>
                      İptal et
                    </button>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      <div className="pad">
        <button className="danger" disabled={!kapali || remove.isPending} onClick={deletePerson}>
          {kapali ? "Kişiyi defterden kaldır" : "Hesap kapanınca kaldırılabilir"}
        </button>
      </div>

      <button className="fab" onClick={() => nav(`/kisi/${personId}/ekle`)}>
        Kayıt ekle
      </button>
    </>
  );
}
