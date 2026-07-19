import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import PersonModal from "../components/PersonModal";
import SettingsModal from "../components/SettingsModal";
import { balanceLabel, balanceTone, hhmm, itemsSummary, money, shortDate, signedMoney } from "../lib/format";
import { useToast } from "../lib/toast";

export default function People() {
  const [q, setQ] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(() => new Date());
  const [refreshing, setRefreshing] = useState(false);
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();

  const people = useQuery({
    queryKey: ["persons", q],
    queryFn: () => api.persons(q || undefined),
  });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.getSettings });

  const rows = people.data ?? [];
  const businessName = settings.data?.business_name ?? "Hesaplık";

  const toplamAlacak = rows
    .filter((p) => Number(p.balance_try) > 0)
    .reduce((acc, p) => acc + Number(p.balance_try), 0);
  const toplamBorc = rows
    .filter((p) => Number(p.balance_try) < 0)
    .reduce((acc, p) => acc + Math.abs(Number(p.balance_try)), 0);

  async function refresh() {
    setRefreshing(true);
    await qc.invalidateQueries();
    setUpdatedAt(new Date());
    toast("Sistem güncellendi", "success");
    setRefreshing(false);
  }

  return (
    <div className="layout">
      <aside className="side-panel">
        <h1 className="side-title">{businessName}</h1>
        <p className="side-subtitle">Hesap defteri</p>

        <div className="metrics">
          <div className="metric-card">
            <div className="metric-label">Kişi sayısı</div>
            <div className="metric-value">{rows.length}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Toplam alacak</div>
            <div className="metric-value borc">{money(toplamAlacak)}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Toplam borç</div>
            <div className="metric-value tahsilat">{money(toplamBorc)}</div>
          </div>
        </div>

        <div className="side-actions">
          <button
            className="side-icon-btn"
            onClick={refresh}
            disabled={refreshing}
            aria-label="Yenile"
            title="Yenile"
          >
            ⟳
          </button>
          <button
            className="side-icon-btn"
            onClick={() => setShowSettings(true)}
            aria-label="Ayarlar"
            title="Ayarlar"
          >
            ⚙
          </button>
          <span className="side-updated">Güncellendi · {hhmm(updatedAt)}</span>
        </div>
      </aside>

      <div className="side-main">
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

        <button className="fab" onClick={() => setShowNew(true)}>
          Kişi ekle
        </button>

        {showNew && <PersonModal onClose={() => setShowNew(false)} />}
        {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
      </div>
    </div>
  );
}
