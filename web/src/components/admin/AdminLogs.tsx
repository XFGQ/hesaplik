/* Loglar — audit_log tablosu: "kim ne zaman neyi değiştirdi" (restore,
 * kişi düzenleme/arşivleme, ters kayıt, hareket arşivleme...). Container
 * stdout logları DEĞİL, yalnızca DB denetim kaydı. Satıra tıklayınca
 * before/after JSON'ı açılır.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { adminApi, Unauthorized, type AuditLogFilters, type AuditLogRow } from "../../api/admin";

const SAYFA = 50;

const ACTION_LABEL: Record<string, string> = {
  add_debt: "borç eklendi",
  add_payment: "tahsilat eklendi",
  reverse: "ters kayıt",
  archive_transaction: "hareket silindi",
  edit_person: "kişi düzenlendi",
  archive_person: "kişi silindi",
  restore_requested: "geri yükleme istendi",
};

function stamp(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export default function AdminLogs({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [actor, setActor] = useState("");
  const [actorInput, setActorInput] = useState("");
  const [action, setAction] = useState("");
  const [entity, setEntity] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<number | null>(null);

  const filters: AuditLogFilters = {
    actor: actor || undefined,
    action: action || undefined,
    entity: entity || undefined,
    from: from || undefined,
    to: to || undefined,
    limit: SAYFA,
    offset: page * SAYFA,
  };

  const q = useQuery({
    queryKey: ["admin-audit-log", filters],
    queryFn: () => adminApi.auditLog(filters),
    retry: false,
  });

  useEffect(() => {
    if (q.error instanceof Unauthorized) onUnauthorized();
  }, [q.error, onUnauthorized]);

  function filtre<T>(setter: (v: T) => void) {
    return (value: T) => {
      setter(value);
      setPage(0);
      setOpen(null);
    };
  }

  const rows = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const sonSayfa = Math.max(0, Math.ceil(total / SAYFA) - 1);

  return (
    <div className="admin-flow">
      <div className="admin-filters">
        <label className="admin-filter">
          <span>Kim</span>
          <input
            type="search"
            value={actorInput}
            placeholder="actor"
            onChange={(e) => setActorInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && filtre(setActor)(actorInput.trim())}
            onBlur={() => filtre(setActor)(actorInput.trim())}
          />
        </label>

        <label className="admin-filter">
          <span>İşlem</span>
          <select value={action} onChange={(e) => filtre(setAction)(e.target.value)}>
            <option value="">Hepsi</option>
            {Object.entries(ACTION_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>

        <label className="admin-filter">
          <span>Varlık</span>
          <select value={entity} onChange={(e) => filtre(setEntity)(e.target.value)}>
            <option value="">Hepsi</option>
            <option value="transactions">Hareketler</option>
            <option value="persons">Kişiler</option>
            <option value="restore">Geri yükleme</option>
          </select>
        </label>

        <label className="admin-filter">
          <span>Başlangıç</span>
          <input type="date" value={from} onChange={(e) => filtre(setFrom)(e.target.value)} />
        </label>

        <label className="admin-filter">
          <span>Bitiş</span>
          <input type="date" value={to} onChange={(e) => filtre(setTo)(e.target.value)} />
        </label>

        <button
          className="admin-btn"
          onClick={() => {
            setActor("");
            setActorInput("");
            setAction("");
            setEntity("");
            setFrom("");
            setTo("");
            setPage(0);
            setOpen(null);
          }}
        >
          Temizle
        </button>

        <button className="admin-btn" onClick={() => q.refetch()} disabled={q.isFetching}>
          {q.isFetching ? "Yükleniyor…" : "Yenile"}
        </button>
      </div>

      {q.isError && !(q.error instanceof Unauthorized) && (
        <p className="error">{(q.error as Error).message}</p>
      )}

      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th className="admin-col-time">Zaman</th>
              <th>İşlem</th>
              <th>Varlık</th>
              <th>Kim</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <LogRowView
                key={row.id}
                row={row}
                open={open === row.id}
                onToggle={() => setOpen(open === row.id ? null : row.id)}
              />
            ))}
          </tbody>
        </table>

        {!q.isLoading && rows.length === 0 && (
          <p className="admin-empty">Bu filtrelerle denetim kaydı yok.</p>
        )}
      </div>

      <div className="admin-pager">
        <span className="muted">
          {total === 0
            ? "0 kayıt"
            : `${page * SAYFA + 1}–${page * SAYFA + rows.length} / ${total} kayıt`}
        </span>
        <div className="admin-pager-btns">
          <button className="admin-btn" onClick={() => setPage((p) => p - 1)} disabled={page <= 0}>
            ← Önceki
          </button>
          <button
            className="admin-btn"
            onClick={() => setPage((p) => p + 1)}
            disabled={page >= sonSayfa}
          >
            Sonraki →
          </button>
        </div>
      </div>
    </div>
  );
}

function LogRowView({
  row,
  open,
  onToggle,
}: {
  row: AuditLogRow;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr className={`admin-row ${open ? "admin-row-open" : ""}`} onClick={onToggle}>
        <td className="admin-col-time admin-mono">{stamp(row.at)}</td>
        <td>
          <span className="admin-badge admin-kind-edit">{ACTION_LABEL[row.action] ?? row.action}</span>
        </td>
        <td className="muted">
          {row.entity}
          {row.entity_id && <span className="admin-mono"> #{row.entity_id}</span>}
        </td>
        <td className="muted">{row.actor}</td>
      </tr>

      {open && (
        <tr className="admin-detail-row">
          <td colSpan={4}>
            <div className="admin-detail">
              <section>
                <h3>Önce</h3>
                <pre className="admin-json">
                  {row.before ? JSON.stringify(row.before, null, 2) : "—"}
                </pre>
              </section>
              <section>
                <h3>Sonra</h3>
                <pre className="admin-json">
                  {row.after ? JSON.stringify(row.after, null, 2) : "—"}
                </pre>
              </section>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
