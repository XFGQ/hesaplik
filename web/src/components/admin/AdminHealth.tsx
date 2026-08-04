/* Sistem Sağlığı — "ne ayakta, ne değil" tek bakışta.
 *
 * Üstte tek cümlelik genel özet (yeşil/sarı/kırmızı), altında her bileşen
 * için bir kart: durum rozeti + tek satır özet + ayrıntı satırları.
 *
 * 30 saniyede bir kendiliğinden yenilenir; sekme arka plandayken durur
 * (React Query varsayılanı) — kimsenin bakmadığı ekran için Ollama'ya ve
 * restic'e boşuna gidilmesin. Elle "Yenile" de var.
 *
 * measured=false olan kartlar "dolaylı" etiketi taşır: bot ayrı bir
 * container'da çalışır, canlılığı yalnızca son gelen mesajdan tahmin
 * edilir. Panel bunu gizlemez — dolaylı bir ipucu kesin bilgi sanılmasın.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { adminApi, Unauthorized, type HealthComponent } from "../../api/admin";
import { hhmm } from "../../lib/format";

const YENILEME_MS = 30_000;

const STATUS_LABEL: Record<string, string> = {
  ok: "çalışıyor",
  uyari: "uyarı",
  hata: "hata",
  bilgi: "bilgi",
};

export default function AdminHealth({ onUnauthorized }: { onUnauthorized: () => void }) {
  const q = useQuery({
    queryKey: ["admin-health"],
    queryFn: () => adminApi.health(),
    refetchInterval: YENILEME_MS,
    retry: false,
  });

  useEffect(() => {
    if (q.error instanceof Unauthorized) onUnauthorized();
  }, [q.error, onUnauthorized]);

  const data = q.data;

  return (
    <div className="admin-health">
      <div className={`admin-health-top admin-health-top-${data?.overall ?? "bekle"}`}>
        <div>
          <strong>{data ? data.overall_text : q.isLoading ? "Kontrol ediliyor…" : "—"}</strong>
          <span className="muted">
            {data
              ? `Son kontrol ${hhmm(new Date(data.checked_at))} · ${YENILEME_MS / 1000} sn'de bir yenilenir`
              : "Sistem sağlığı okunuyor"}
          </span>
        </div>
        <button className="admin-btn" onClick={() => q.refetch()} disabled={q.isFetching}>
          {q.isFetching ? "Kontrol ediliyor…" : "Yenile"}
        </button>
      </div>

      {q.isError && !(q.error instanceof Unauthorized) && (
        <p className="error">{(q.error as Error).message}</p>
      )}

      <div className="admin-health-grid">
        {(data?.components ?? []).map((c) => (
          <HealthCard key={c.id} c={c} />
        ))}
      </div>
    </div>
  );
}

function HealthCard({ c }: { c: HealthComponent }) {
  return (
    <section className={`admin-health-card admin-health-${c.status}`}>
      <header>
        <h3>{c.label}</h3>
        <span className={`admin-badge admin-health-badge-${c.status}`}>
          {STATUS_LABEL[c.status] ?? c.status}
        </span>
      </header>

      <p className="admin-health-summary">{c.summary}</p>

      <dl className="admin-health-details">
        {c.details.map((d) => (
          <div className="admin-line" key={d.label}>
            <dt className="admin-line-label">{d.label}</dt>
            <dd className="admin-line-value admin-mono">{d.value}</dd>
          </div>
        ))}
      </dl>

      {!c.measured && <span className="admin-tag admin-health-indirect">dolaylı ölçüm</span>}
      {c.note && <p className="admin-health-note">{c.note}</p>}
    </section>
  );
}
