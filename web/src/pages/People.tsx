import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { PersonRow } from "../api/types";
import DeletePersonModal from "../components/DeletePersonModal";
import PersonModal from "../components/PersonModal";
import RowMenu from "../components/RowMenu";
import { ActionBarMain } from "../lib/actionBar";
import { balanceLabel, balanceTone, itemsSummary, shortDate, signedMoney } from "../lib/format";

export default function People() {
  const [q, setQ] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [editingPerson, setEditingPerson] = useState<PersonRow | null>(null);
  const [deletingPerson, setDeletingPerson] = useState<PersonRow | null>(null);
  const nav = useNavigate();

  const people = useQuery({
    queryKey: ["persons", q],
    queryFn: () => api.persons(q || undefined),
  });

  const rows = people.data ?? [];

  function menuItems(p: PersonRow) {
    return [
      { label: "Düzenle", onClick: () => setEditingPerson(p) },
      { label: "Sil", onClick: () => setDeletingPerson(p), danger: true },
    ];
  }

  return (
    <>
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
                <th aria-hidden="true"></th>
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
                  <td className="row-menu-cell">
                    <RowMenu items={menuItems(p)} />
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
            <div className="row">
              <button className="row-main" onClick={() => nav(`/kisi/${p.id}`)}>
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
              <RowMenu items={menuItems(p)} />
            </div>
          </li>
        ))}
      </ul>

      {/* Buton alt eylem barının SOL yuvasına çizilir (sağda sohbet balonu
          durur). Bar Layout'ta, opak ve sabit — bkz. lib/actionBar.tsx. */}
      <ActionBarMain>
        <button className="fab" onClick={() => setShowNew(true)}>
          Kişi ekle
        </button>
      </ActionBarMain>

      {showNew && <PersonModal onClose={() => setShowNew(false)} />}
      {editingPerson && (
        <PersonModal person={editingPerson} onClose={() => setEditingPerson(null)} />
      )}
      {deletingPerson && (
        <DeletePersonModal person={deletingPerson} onClose={() => setDeletingPerson(null)} />
      )}
    </>
  );
}
