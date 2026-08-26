/* LLM İzleme — "LLM Yönetimi"nden (yukarıdaki bölüm) AYRI: o bir switch
 * (hangi motor aktif), bu bir analitik. Yalnızca parse_source='llm' düşen
 * (regex'in çözemediği, yavaş yola giden) mesajları gösterir: ne zaman,
 * ne kadar sürdü, başarılı mı. Veri kaynağı raw_messages, aynı akış
 * bölümünün (AdminFlow) izleme alanları — bkz. app/services/message_trace.py.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { adminApi, Unauthorized, type LlmMonitorFilters, type LlmMonitorRow } from "../../api/admin";

const SAYFA = 50;

const KIND_LABEL: Record<string, string> = {
  debt: "borç",
  payment: "tahsilat",
  query: "sorgu",
  edit: "düzenleme",
  archive: "silme",
  none: "—",
};

const OUTCOME_LABEL: Record<string, string> = {
  kaydedildi: "başarılı · kaydedildi",
  yanitlandi: "başarılı · yanıtlandı",
  soru_soruldu: "başarılı · soru soruldu",
  hata: "başarısız · hata",
  yok_sayildi: "başarısız · anlaşılamadı",
};

function stamp(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function yuzde(v: number | null): string {
  return v == null ? "—" : `%${Math.round(v * 100)}`;
}

export default function AdminLlmMonitor({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [outcome, setOutcome] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(0);

  const filters: LlmMonitorFilters = {
    outcome: outcome || undefined,
    from: from || undefined,
    to: to || undefined,
    limit: SAYFA,
    offset: page * SAYFA,
  };

  const q = useQuery({
    queryKey: ["admin-llm-monitor", filters],
    queryFn: () => adminApi.llmMonitor(filters),
    retry: false,
  });

  useEffect(() => {
    if (q.error instanceof Unauthorized) onUnauthorized();
  }, [q.error, onUnauthorized]);

  function filtre<T>(setter: (v: T) => void) {
    return (value: T) => {
      setter(value);
      setPage(0);
    };
  }

  const rows = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const stats = q.data?.stats;
  const sonSayfa = Math.max(0, Math.ceil(total / SAYFA) - 1);

  return (
    <div className="admin-flow">
      <div className="admin-stat-row">
        <StatTile label="Toplam LLM çağrısı" value={stats ? String(stats.total_calls) : "—"} />
        <StatTile
          label="Ortalama süre"
          value={stats?.avg_parse_ms != null ? `${Math.round(stats.avg_parse_ms)} ms` : "—"}
        />
        <StatTile label="Başarı oranı" value={stats ? yuzde(stats.success_rate) : "—"} />
        <StatTile
          label="Başarılı / başarısız"
          value={stats ? `${stats.success_count} / ${stats.failure_count}` : "—"}
        />
      </div>

      <div className="admin-filters">
        <label className="admin-filter">
          <span>Sonuç</span>
          <select value={outcome} onChange={(e) => filtre(setOutcome)(e.target.value)}>
            <option value="">Hepsi</option>
            <option value="kaydedildi">Kaydedildi</option>
            <option value="yanitlandi">Yanıtlandı</option>
            <option value="soru_soruldu">Soru soruldu</option>
            <option value="hata">Hata</option>
            <option value="yok_sayildi">Anlaşılamadı</option>
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
            setOutcome("");
            setFrom("");
            setTo("");
            setPage(0);
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
              <th>Cümle</th>
              <th className="admin-col-detect">Algılanan</th>
              <th className="admin-col-time">Süre</th>
              <th className="admin-col-outcome">Sonuç</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <LlmRow key={row.id} row={row} />
            ))}
          </tbody>
        </table>

        {!q.isLoading && rows.length === 0 && (
          <p className="admin-empty">Bu filtrelerle LLM'e düşmüş mesaj yok.</p>
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

function LlmRow({ row }: { row: LlmMonitorRow }) {
  const kind = row.detected_kind ?? "none";
  const basarili = row.outcome === "kaydedildi" || row.outcome === "yanitlandi" || row.outcome === "soru_soruldu";

  return (
    <tr>
      <td className="admin-col-time admin-mono">{stamp(row.received_at)}</td>
      <td className="admin-raw">{row.text ?? <span className="muted">(metin yok)</span>}</td>
      <td className="admin-col-detect">
        <span className={`admin-badge admin-kind-${kind}`}>{KIND_LABEL[kind] ?? kind}</span>
        {row.detected_person && <span className="admin-detect-person">{row.detected_person}</span>}
      </td>
      <td className="admin-col-time admin-mono">{row.parse_ms != null ? `${row.parse_ms} ms` : "—"}</td>
      <td className="admin-col-outcome">
        <span className={`admin-badge ${basarili ? "admin-outcome-kaydedildi" : "admin-outcome-hata"}`}>
          {OUTCOME_LABEL[row.outcome ?? ""] ?? row.outcome ?? "—"}
        </span>
      </td>
    </tr>
  );
}

export function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="admin-stat-tile">
      <span className="admin-stat-label">{label}</span>
      <span className="admin-stat-value admin-mono">{value}</span>
    </div>
  );
}
