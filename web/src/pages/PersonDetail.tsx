import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import { balanceLabel, balanceTone, money, qty, shortDate } from "../lib/format";

export default function PersonDetail() {
  const { id } = useParams();
  const personId = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();

  const balance = useQuery({
    queryKey: ["balance", personId],
    queryFn: () => api.balance(personId),
  });
  const txs = useQuery({
    queryKey: ["transactions", personId],
    queryFn: () => api.transactions(personId),
  });
  const people = useQuery({ queryKey: ["persons", ""], queryFn: () => api.persons() });
  const person = people.data?.find((p) => p.id === personId);

  const reverse = useMutation({
    mutationFn: ({ txId, reason }: { txId: number; reason: string }) =>
      api.reverse(txId, reason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["balance", personId] });
      qc.invalidateQueries({ queryKey: ["transactions", personId] });
      qc.invalidateQueries({ queryKey: ["persons"] });
    },
  });

  function cancel(txId: number) {
    const reason = window.prompt("İptal sebebi (kayıt silinmez, ters kayıt açılır)");
    if (reason && reason.trim().length >= 3) {
      reverse.mutate({ txId, reason: reason.trim() });
    }
  }

  const tone = balance.data ? balanceTone(balance.data.balance_try) : "zero";

  return (
    <>
      <div className="bar">
        <button className="back" onClick={() => nav("/")}>
          ← Defter
        </button>
        <h1>{person?.full_name ?? "Kişi"}</h1>
      </div>

      <div className="balance">
        <div className="balance-label">
          {balance.data ? balanceLabel(balance.data.balance_try) : "hesaplanıyor"}
        </div>
        <div className={`balance-amount ${tone}`}>
          {balance.data ? money(balance.data.balance_try) : "—"}
        </div>
        {balance.data && balance.data.items.length > 0 && (
          <div className="chips">
            {balance.data.items.map((it) => (
              <span className="chip" key={it.product_name}>
                {qty(it.qty)} {it.unit} {it.product_name.toLocaleLowerCase("tr")}
              </span>
            ))}
          </div>
        )}
      </div>

      {reverse.isError && <p className="error">{(reverse.error as Error).message}</p>}

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
            ? t.lines
                .map((l) => `${qty(l.qty)} ${l.unit} ${l.product_name.toLocaleLowerCase("tr")}`)
                .join(", ")
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
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
                >
                  <span className="muted">
                    {shortDate(t.occurred_at)}
                    {t.reverses_id ? ` · #${t.reverses_id} iptali` : ""}
                    {t.is_reversed ? " · iptal edildi" : ""}
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

      <button className="fab" onClick={() => nav(`/kisi/${personId}/ekle`)}>
        Kayıt ekle
      </button>
    </>
  );
}
