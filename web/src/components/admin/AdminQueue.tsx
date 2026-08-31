/* İstek Kuyruğu — pending_requests tablosu (CLAUDE.md > "Çoklu istek —
 * kalıcı istek kuyruğu"). Tek mesajdan çıkan birden çok işlem burada
 * sırayla işlenir; bot yeniden başlasa/soru sorup beklese bile kaybolmaz.
 * Salt okunur görünüm: durum sayıları + filtrelenebilir liste.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { adminApi, Unauthorized, type QueueRow } from "../../api/admin";
import { StatTile } from "./AdminLlmMonitor";

const SAYFA = 50;

const DURUM_LABEL: Record<string, string> = {
  beklemede: "beklemede",
  isleniyor: "işleniyor",
  tamamlandi: "tamamlandı",
  basarisiz: "başarısız",
  iptal: "iptal",
};

function stamp(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function AdminQueue({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [durum, setDurum] = useState("");
  const [page, setPage] = useState(0);

  const q = useQuery({
    queryKey: ["admin-queue", durum, page],
    queryFn: () => adminApi.queue(durum || undefined, SAYFA, page * SAYFA),
    retry: false,
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (q.error instanceof Unauthorized) onUnauthorized();
  }, [q.error, onUnauthorized]);

  function filtre(value: string) {
    setDurum(value);
    setPage(0);
  }

  const rows = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const counts = q.data?.counts;
  const sonSayfa = Math.max(0, Math.ceil(total / SAYFA) - 1);

  return (
    <div className="admin-flow">
      <div className="admin-stat-row">
        <StatTile label="Beklemede" value={counts ? String(counts.beklemede) : "—"} />
        <StatTile label="İşleniyor" value={counts ? String(counts.isleniyor) : "—"} />
        <StatTile label="Başarısız" value={counts ? String(counts.basarisiz) : "—"} />
        <StatTile label="Tamamlandı" value={counts ? String(counts.tamamlandi) : "—"} />
        <StatTile label="İptal" value={counts ? String(counts.iptal) : "—"} />
      </div>

      <div className="admin-filters">
        <label className="admin-filter">
          <span>Durum</span>
          <select value={durum} onChange={(e) => filtre(e.target.value)}>
            <option value="">Hepsi</option>
            <option value="beklemede">Beklemede</option>
            <option value="isleniyor">İşleniyor</option>
            <option value="basarisiz">Başarısız</option>
            <option value="tamamlandi">Tamamlandı</option>
            <option value="iptal">İptal</option>
          </select>
        </label>

        <button className="admin-btn" onClick={() => filtre("")}>
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
              <th>Metin</th>
              <th className="admin-col-time">Sıra</th>
              <th className="admin-col-outcome">Durum</th>
              <th>Sonuç / hata</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <QueueRowView key={row.id} row={row} />
            ))}
          </tbody>
        </table>

        {!q.isLoading && rows.length === 0 && (
          <p className="admin-empty">Bu filtrelerle gösterilecek istek yok.</p>
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

function QueueRowView({ row }: { row: QueueRow }) {
  return (
    <tr>
      <td className="admin-col-time admin-mono">{stamp(row.created_at)}</td>
      <td className="admin-raw">{row.raw_text}</td>
      <td className="admin-col-time admin-mono">{row.sira_no}</td>
      <td className="admin-col-outcome">
        <span className={`admin-badge admin-queue-${row.durum}`}>{DURUM_LABEL[row.durum] ?? row.durum}</span>
      </td>
      <td>{row.hata ?? row.sonuc ?? <span className="muted">—</span>}</td>
    </tr>
  );
}
