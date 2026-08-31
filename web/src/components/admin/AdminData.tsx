/* Kişiler & İşlemler — salt okunur veri gezgini. İki sekme:
 *   "Aktif defter": persons + bakiye (kişi listesiyle aynı sorgu,
 *     app/services/queries.py::list_persons_with_balance). Satıra tıklayınca
 *     o kişinin TÜM hareketleri (durumu ne olursa olsun) açılır.
 *   "Arşiv": "Sil" = arşivle kararının (CLAUDE.md) denetim görünümü —
 *     silinen kişiler ve silinen hareketler, kim/ne zaman/neden bilgisiyle.
 * Hiçbir yazma işlemi yok; bu ekrandan hiçbir şey değiştirilemez.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  adminApi,
  Unauthorized,
  type AdminPersonRow,
  type AdminTxRow,
  type ArchivedPersonRow,
  type ArchivedTransactionRow,
} from "../../api/admin";
import { balanceLabel, balanceTone, itemsSummary, money, qty, shortDate } from "../../lib/format";

type Tab = "aktif" | "arsiv";
type ArsivTab = "kisiler" | "hareketler";

export default function AdminData({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [tab, setTab] = useState<Tab>("aktif");

  return (
    <div className="admin-flow">
      <div className="admin-tabs">
        <button
          className={`admin-tab ${tab === "aktif" ? "admin-tab-active" : ""}`}
          onClick={() => setTab("aktif")}
        >
          Aktif defter
        </button>
        <button
          className={`admin-tab ${tab === "arsiv" ? "admin-tab-active" : ""}`}
          onClick={() => setTab("arsiv")}
        >
          Arşiv
        </button>
      </div>

      {tab === "aktif" ? (
        <AktifKisiler onUnauthorized={onUnauthorized} />
      ) : (
        <Arsiv onUnauthorized={onUnauthorized} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- aktif kişiler

function AktifKisiler({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState<number | null>(null);

  const query = useQuery({
    queryKey: ["admin-persons", q, filter],
    queryFn: () => adminApi.persons({ q: q || undefined, filter }),
    retry: false,
  });

  useEffect(() => {
    if (query.error instanceof Unauthorized) onUnauthorized();
  }, [query.error, onUnauthorized]);

  const rows = query.data?.items ?? [];

  return (
    <>
      <div className="admin-filters">
        <label className="admin-filter">
          <span>Ara</span>
          <input
            type="search"
            value={search}
            placeholder="ad, telefon"
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && setQ(search.trim())}
            onBlur={() => setQ(search.trim())}
          />
        </label>

        <label className="admin-filter">
          <span>Kapsam</span>
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">Hepsi</option>
            <option value="debtors">Borçlular</option>
            <option value="creditors">Alacaklılar</option>
          </select>
        </label>

        <button className="admin-btn" onClick={() => query.refetch()} disabled={query.isFetching}>
          {query.isFetching ? "Yükleniyor…" : "Yenile"}
        </button>
      </div>

      {query.isError && !(query.error instanceof Unauthorized) && (
        <p className="error">{(query.error as Error).message}</p>
      )}

      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Ad Soyad</th>
              <th>İletişim</th>
              <th className="admin-col-detect">Açık kalemler</th>
              <th className="admin-col-outcome">Bakiye</th>
              <th className="admin-col-time">Son işlem</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <PersonRowView
                key={p.id}
                person={p}
                open={open === p.id}
                onToggle={() => setOpen(open === p.id ? null : p.id)}
              />
            ))}
          </tbody>
        </table>

        {!query.isLoading && rows.length === 0 && (
          <p className="admin-empty">Bu filtrelerle kişi yok.</p>
        )}
      </div>

      <p className="muted" style={{ marginTop: 8 }}>
        {rows.length} kişi
      </p>
    </>
  );
}

function PersonRowView({
  person,
  open,
  onToggle,
}: {
  person: AdminPersonRow;
  open: boolean;
  onToggle: () => void;
}) {
  const tone = balanceTone(person.balance_try);

  return (
    <>
      <tr className={`admin-row ${open ? "admin-row-open" : ""}`} onClick={onToggle}>
        <td>{person.full_name}</td>
        <td className="muted">
          {[person.phone, person.district].filter(Boolean).join(" · ") || "—"}
        </td>
        <td className="admin-col-detect">
          {person.items.length ? itemsSummary(person.items) : <span className="muted">—</span>}
        </td>
        <td className={`admin-col-outcome admin-mono ${tone}`}>
          {money(person.balance_try)}
          <span className="muted"> {balanceLabel(person.balance_try)}</span>
        </td>
        <td className="admin-col-time muted">
          {person.last_activity ? shortDate(person.last_activity) : "—"}
        </td>
      </tr>

      {open && (
        <tr className="admin-detail-row">
          <td colSpan={5}>
            <PersonTransactions personId={person.id} />
          </td>
        </tr>
      )}
    </>
  );
}

function PersonTransactions({ personId }: { personId: number }) {
  const q = useQuery({
    queryKey: ["admin-person-tx", personId],
    queryFn: () => adminApi.personTransactions(personId),
    retry: false,
  });

  if (q.isLoading) return <p className="muted" style={{ padding: 14 }}>Yükleniyor…</p>;
  if (q.isError) return <p className="error" style={{ padding: 14 }}>Hareketler alınamadı.</p>;

  const items = q.data?.items ?? [];
  if (items.length === 0) {
    return <p className="admin-empty">Hiç hareket yok.</p>;
  }

  return (
    <div className="admin-detail">
      <table className="admin-table admin-subtable">
        <thead>
          <tr>
            <th className="admin-col-time">Tarih</th>
            <th className="admin-col-detect">Yön</th>
            <th>Kalemler</th>
            <th className="admin-col-outcome">Tutar</th>
            <th className="admin-col-outcome">Durum</th>
          </tr>
        </thead>
        <tbody>
          {items.map((t) => (
            <TxRowView key={t.id} tx={t} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

const STATUS_LABEL: Record<string, string> = {
  CONFIRMED: "onaylı",
  PENDING: "bekliyor",
  REJECTED: "reddedildi",
};

function TxRowView({ tx }: { tx: AdminTxRow }) {
  const isDebt = tx.kind === "DEBIT";
  return (
    <tr>
      <td className="admin-col-time admin-mono muted">{shortDate(tx.occurred_at)}</td>
      <td className="admin-col-detect">
        <span className={`admin-badge admin-kind-${isDebt ? "debt" : "payment"}`}>
          {isDebt ? "borç" : "tahsilat"}
        </span>
        {tx.reverses_id != null && <span className="admin-tag">ters kayıt</span>}
      </td>
      <td>
        {tx.lines.length
          ? tx.lines.map((li) => `${qty(li.qty)} ${li.unit} ${li.product_name}`).join(" · ")
          : tx.note || <span className="muted">—</span>}
      </td>
      <td className="admin-col-outcome admin-mono">{money(tx.amount_try)}</td>
      <td className="admin-col-outcome">
        <span className="muted">{STATUS_LABEL[tx.status] ?? tx.status}</span>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------- arşiv

function Arsiv({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [tab, setTab] = useState<ArsivTab>("kisiler");

  return (
    <>
      <div className="admin-tabs admin-subtabs">
        <button
          className={`admin-tab ${tab === "kisiler" ? "admin-tab-active" : ""}`}
          onClick={() => setTab("kisiler")}
        >
          Silinen kişiler
        </button>
        <button
          className={`admin-tab ${tab === "hareketler" ? "admin-tab-active" : ""}`}
          onClick={() => setTab("hareketler")}
        >
          Silinen hareketler
        </button>
      </div>

      {tab === "kisiler" ? (
        <ArsivKisiler onUnauthorized={onUnauthorized} />
      ) : (
        <ArsivHareketler onUnauthorized={onUnauthorized} />
      )}
    </>
  );
}

function ArsivKisiler({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");

  const query = useQuery({
    queryKey: ["admin-archived-persons", q],
    queryFn: () => adminApi.archivedPersons(q || undefined),
    retry: false,
  });

  useEffect(() => {
    if (query.error instanceof Unauthorized) onUnauthorized();
  }, [query.error, onUnauthorized]);

  const rows = query.data?.items ?? [];

  return (
    <>
      <div className="admin-filters">
        <label className="admin-filter">
          <span>Ara</span>
          <input
            type="search"
            value={search}
            placeholder="ad"
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && setQ(search.trim())}
            onBlur={() => setQ(search.trim())}
          />
        </label>
      </div>

      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Ad Soyad</th>
              <th>İletişim</th>
              <th className="admin-col-outcome">Arşivlenen bakiye</th>
              <th>Sebep</th>
              <th className="admin-col-time">Silindi</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <ArchivedPersonRowView key={r.id} row={r} />
            ))}
          </tbody>
        </table>

        {!query.isLoading && rows.length === 0 && (
          <p className="admin-empty">Silinmiş kişi yok.</p>
        )}
      </div>
    </>
  );
}

function ArchivedPersonRowView({ row }: { row: ArchivedPersonRow }) {
  const tone = balanceTone(row.balance_try);
  return (
    <tr>
      <td>{row.full_name}</td>
      <td className="muted">{[row.phone, row.district].filter(Boolean).join(" · ") || "—"}</td>
      <td className={`admin-col-outcome admin-mono ${tone}`}>{money(row.balance_try)}</td>
      <td className="muted">{row.archive_reason || "—"}</td>
      <td className="admin-col-time muted">{shortDate(row.archived_at)}</td>
    </tr>
  );
}

function ArsivHareketler({ onUnauthorized }: { onUnauthorized: () => void }) {
  const query = useQuery({
    queryKey: ["admin-archived-transactions"],
    queryFn: () => adminApi.archivedTransactions(undefined),
    retry: false,
  });

  useEffect(() => {
    if (query.error instanceof Unauthorized) onUnauthorized();
  }, [query.error, onUnauthorized]);

  const rows = query.data?.items ?? [];

  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            <th className="admin-col-time">Tarih</th>
            <th className="admin-col-detect">Yön</th>
            <th>Not</th>
            <th className="admin-col-outcome">Tutar</th>
            <th>Sebep</th>
            <th className="admin-col-time">Silindi</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <ArchivedTxRowView key={r.id} row={r} />
          ))}
        </tbody>
      </table>

      {!query.isLoading && rows.length === 0 && (
        <p className="admin-empty">Silinmiş hareket yok.</p>
      )}
    </div>
  );
}

function ArchivedTxRowView({ row }: { row: ArchivedTransactionRow }) {
  const isDebt = row.kind === "DEBIT";
  return (
    <tr>
      <td className="admin-col-time admin-mono muted">{shortDate(row.occurred_at)}</td>
      <td className="admin-col-detect">
        <span className={`admin-badge admin-kind-${isDebt ? "debt" : "payment"}`}>
          {isDebt ? "borç" : "tahsilat"}
        </span>
      </td>
      <td className="muted">{row.note || "—"}</td>
      <td className="admin-col-outcome admin-mono">{money(row.amount_try)}</td>
      <td className="muted">{row.archive_reason || "—"}</td>
      <td className="admin-col-time muted">{shortDate(row.archived_at)}</td>
    </tr>
  );
}
