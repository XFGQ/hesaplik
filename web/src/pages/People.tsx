import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { balanceLabel, balanceTone, money } from "../lib/format";

export default function People() {
  const [q, setQ] = useState("");
  const nav = useNavigate();
  const qc = useQueryClient();

  const people = useQuery({
    queryKey: ["persons", q],
    queryFn: () => api.persons(q || undefined),
  });

  const add = useMutation({
    mutationFn: (name: string) => api.createPerson(name),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["persons"] });
      nav(`/kisi/${p.id}`);
    },
  });

  function newPerson() {
    const name = window.prompt("Kişinin adı soyadı");
    if (name && name.trim().length >= 2) add.mutate(name.trim());
  }

  return (
    <>
      <div className="bar">
        <h1>Hesaplık</h1>
      </div>

      <div className="pad">
        <input
          type="search"
          placeholder="Kişi ara"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Kişi ara"
        />
      </div>

      {people.isError && <p className="error">Liste yüklenemedi. Bağlantını kontrol et.</p>}

      {people.data?.length === 0 && (
        <div className="empty">
          <p>{q ? "Bu isimde kimse yok." : "Defter boş."}</p>
          <p className="muted">Aşağıdan ilk kişini ekle.</p>
        </div>
      )}

      <ul className="ledger">
        {people.data?.map((p) => {
          const tone = balanceTone(p.balance_try);
          return (
            <li key={p.id}>
              <button className="row" onClick={() => nav(`/kisi/${p.id}`)}>
                <span className="row-name">
                  {p.full_name}
                  <span className="row-sub">{balanceLabel(p.balance_try)}</span>
                </span>
                <span className="row-leader" aria-hidden="true" />
                <span className={`row-amount ${tone}`}>{money(p.balance_try)}</span>
              </button>
            </li>
          );
        })}
      </ul>

      <button className="fab" onClick={newPerson}>
        Kişi ekle
      </button>
    </>
  );
}
