/* İşlem Akışı — panelin ilk gerçek bölümü.
 *
 * Her satır bir gelen mesajın uçtan uca izi:
 *   [tarih saat]  "ham metin"  →  algılanan (tür + kişi + tutar + regex/llm)
 *                               →  sonuç (kaydedildi / soru soruldu / hata)
 *
 * Satıra tıklanınca altında detay açılır: tam parse çıktısı, parse süresi,
 * kaynak ve varsa oluşan defter kaydı. Veri kaynağı raw_messages'a mesaj
 * işlenirken yazılan izleme alanlarıdır (app/services/message_trace.py).
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { adminApi, Unauthorized, type FlowFilters, type FlowRow } from "../../api/admin";
import { money, qty as fmtQty } from "../../lib/format";

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
  kaydedildi: "kaydedildi",
  yanitlandi: "yanıtlandı",
  soru_soruldu: "soru soruldu",
  hata: "hata",
  yok_sayildi: "anlaşılamadı",
};

function stamp(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function AdminFlow({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [kind, setKind] = useState("");
  const [person, setPerson] = useState("");
  const [personInput, setPersonInput] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<number | null>(null);

  const filters: FlowFilters = {
    kind: kind || undefined,
    person: person || undefined,
    from: from || undefined,
    to: to || undefined,
    limit: SAYFA,
    offset: page * SAYFA,
  };

  const q = useQuery({
    queryKey: ["admin-flow", filters],
    queryFn: () => adminApi.flow(filters),
    retry: false,
  });

  // Oturum düşerse panel şifre ekranına dönsün — "istek başarısız" değil.
  useEffect(() => {
    if (q.error instanceof Unauthorized) onUnauthorized();
  }, [q.error, onUnauthorized]);

  // Filtre değişince ilk sayfaya dön: 3. sayfada 2 sonuçlu bir filtreye
  // geçince boş ekran görünmesin.
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
          <span>Tür</span>
          <select value={kind} onChange={(e) => filtre(setKind)(e.target.value)}>
            <option value="">Hepsi</option>
            <option value="debt">Borç</option>
            <option value="payment">Tahsilat</option>
            <option value="query">Sorgu</option>
            <option value="edit">Düzenleme</option>
            <option value="archive">Silme</option>
            <option value="none">Algılanamadı</option>
          </select>
        </label>

        <label className="admin-filter">
          <span>Kişi</span>
          <input
            type="search"
            value={personInput}
            placeholder="ad ara"
            onChange={(e) => setPersonInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && filtre(setPerson)(personInput.trim())}
            onBlur={() => filtre(setPerson)(personInput.trim())}
          />
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
            setKind("");
            setPerson("");
            setPersonInput("");
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
              <th>Ham metin</th>
              <th className="admin-col-detect">Algılanan</th>
              <th className="admin-col-outcome">Sonuç</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <FlowRowView
                key={row.id}
                row={row}
                open={open === row.id}
                onToggle={() => setOpen(open === row.id ? null : row.id)}
              />
            ))}
          </tbody>
        </table>

        {!q.isLoading && rows.length === 0 && (
          <p className="admin-empty">Bu filtrelerle gösterilecek mesaj yok.</p>
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

function FlowRowView({
  row,
  open,
  onToggle,
}: {
  row: FlowRow;
  open: boolean;
  onToggle: () => void;
}) {
  const kind = row.detected_kind ?? "none";

  return (
    <>
      <tr className={`admin-row ${open ? "admin-row-open" : ""}`} onClick={onToggle}>
        <td className="admin-col-time admin-mono">{stamp(row.received_at)}</td>
        <td className="admin-raw">{row.text ?? <span className="muted">(metin yok)</span>}</td>
        <td className="admin-col-detect">
          <span className={`admin-badge admin-kind-${kind}`}>{KIND_LABEL[kind] ?? kind}</span>
          {row.detected_person && <span className="admin-detect-person">{row.detected_person}</span>}
          {row.detected_amount && (
            <span className="admin-mono admin-detect-amount">{money(row.detected_amount)}</span>
          )}
          {row.parse_source && (
            <span className={`admin-tag admin-src-${row.parse_source}`}>{row.parse_source}</span>
          )}
        </td>
        <td className="admin-col-outcome">
          <span className={`admin-badge admin-outcome-${row.outcome ?? "yok"}`}>
            {OUTCOME_LABEL[row.outcome ?? ""] ?? "—"}
          </span>
        </td>
      </tr>

      {open && (
        <tr className="admin-detail-row">
          <td colSpan={4}>
            <div className="admin-detail">
              <section>
                <h3>Mesaj</h3>
                <Line label="Ham metin" value={row.text} mono />
                <Line label="Kanal" value={row.channel} />
                <Line label="Sohbet" value={row.chat_id} />
                <Line label="Alındı" value={new Date(row.received_at).toLocaleString("tr-TR")} />
                <Line
                  label="İşlendi"
                  value={
                    row.processed_at ? new Date(row.processed_at).toLocaleString("tr-TR") : "—"
                  }
                />
                <Line label="Mesaj no" value={`#${row.id}`} mono />
              </section>

              <section>
                <h3>Algılanan</h3>
                <Line label="Tür" value={KIND_LABEL[kind] ?? kind} />
                <Line label="Kişi" value={row.detected_person} />
                <Line
                  label="Tutar"
                  value={row.detected_amount ? money(row.detected_amount) : null}
                  mono
                />
                <Line label="Ürün" value={row.detected_product} />
                <Line
                  label="Adet"
                  value={
                    row.detected_qty
                      ? `${fmtQty(row.detected_qty)} ${row.detected_unit ?? ""}`.trim()
                      : null
                  }
                  mono
                />
                <Line label="Kaynak" value={row.parse_source} />
                <Line label="Süre" value={row.parse_ms != null ? `${row.parse_ms} ms` : null} mono />
              </section>

              <section>
                <h3>Sonuç</h3>
                <Line label="Durum" value={OUTCOME_LABEL[row.outcome ?? ""] ?? row.outcome} />
                <Line label="Açıklama" value={row.outcome_detail} />
                {row.transaction ? (
                  <>
                    <Line label="Oluşan kayıt" value={`#${row.transaction.id}`} mono />
                    <Line
                      label="Yön"
                      value={row.transaction.kind === "DEBIT" ? "borç" : "tahsilat"}
                    />
                    <Line label="Defterdeki kişi" value={row.transaction.person_name} />
                    <Line label="Tutar" value={money(row.transaction.amount_try)} mono />
                  </>
                ) : (
                  <Line label="Oluşan kayıt" value="— (deftere yazılmadı)" />
                )}
              </section>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function Line({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | null | undefined;
  mono?: boolean;
}) {
  return (
    <div className="admin-line">
      <span className="admin-line-label">{label}</span>
      <span className={`admin-line-value ${mono ? "admin-mono" : ""}`}>
        {value ? value : <span className="muted">—</span>}
      </span>
    </div>
  );
}
