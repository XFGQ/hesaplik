import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { balanceLabel, balanceTone, itemsSummary, shortDate, signedMoney } from "../lib/format";

export default function People() {
  const [q, setQ] = useState("");
  const nav = useNavigate();

  const people = useQuery({
    queryKey: ["persons", q],
    queryFn: () => api.persons(q || undefined),
  });

  const rows = people.data ?? [];
  const toplam = rows.reduce((acc, p) => acc + Number(p.balance_try), 0);

  return (
    <>
      <div className="bar">
        <h1>Hesaplık</h1>
        {rows.length > 0 && (
          <span className={`row-amount ${balanceTone(toplam)}`} title="Toplam bakiye">
            {signedMoney(toplam)}
          </span>
        )}
      </div>

      <div className="pad">
        <input
          type="search"
          placeholder="İsim veya telefon ara"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Kişi ara"
        />
      </div>

      {people.isError && <p className="error">Liste yüklenemedi. Bağlantını kontrol et.</p>}

      {rows.length === 0 && !people.isLoading && (
        <div className="empty">
          <p>{q ? "Bu aramaya uyan kimse yok." : "Defter boş."}</p>
          <p className="muted">Aşağıdan ilk kişini ekle.</p>
        </div>
      )}

      {/* Geniş ekran: tablo. No sütunu yok. */}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Ad Soyad</th>
                <th>Kalemler</th>
                <th style={{ textAlign: "right" }}>Bakiye</th>
                <th>Son işlem</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id} onClick={() => nav(`/kisi/${p.id}`)}>
                  <td>{p.full_name}</td>
                  <td className="muted">{p.items.length ? itemsSummary(p.items) : "—"}</td>
                  <td className={`num ${balanceTone(p.balance_try)}`}>
                    {signedMoney(p.balance_try)}
                    <div className="muted" style={{ fontWeight: 400 }}>
                      {balanceLabel(p.balance_try)}
                    </div>
                  </td>
                  <td className="muted">
                    {p.last_activity ? shortDate(p.last_activity) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Dar ekran: defter satırı */}
      <ul className="ledger only-narrow">
        {rows.map((p) => (
          <li key={p.id}>
            <button className="row" onClick={() => nav(`/kisi/${p.id}`)}>
              <span className="row-name">
                {p.full_name}
                <span className="row-sub">
                  {p.items.length ? itemsSummary(p.items) : balanceLabel(p.balance_try)}
                </span>
              </span>
              <span className="row-leader" aria-hidden="true" />
              <span className={`row-amount ${balanceTone(p.balance_try)}`}>
                {signedMoney(p.balance_try)}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <button className="fab" onClick={() => nav("/kisi/yeni")}>
        Kişi ekle
      </button>
    </>
  );
}
