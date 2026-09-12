/* Yedekleme — depodaki tüm yedekler + "ana veri yap" (geri yükleme) akışı.
 *
 * Üstte özet (kaç yedek, sonuncusu ne zaman, depo nerede, sonraki otomatik
 * yedek tahmini), altında en yeni üstte tablo. Her satırda "Ana veri yap".
 *
 * "Yol A": panel İSTER, host UYGULAR. Onaylanan istek sunucuda bir kuyruğa
 * yazılır (restore_requests); gerçek geri yüklemeyi host'taki izleyici yapar.
 * Bu yüzden onaydan sonra ekran kapanmaz — durum şeridi belirir ve 3 saniyede
 * bir güncellenir: Bekliyor → Yedekleniyor → Yükleniyor → Tamamlandı.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  adminApi,
  Forbidden,
  Unauthorized,
  type BackupSnapshot,
  type RestoreRequest,
  type RestoreStatus,
} from "../../api/admin";
import Modal from "../Modal";

const DURUM_MS = 3_000;

/* app/services/backup.py: PRE_RESTORE_TAG. Geri yüklemeden önce alınan
 * güvenlik yedeği bu etiketle işaretlenir ve listede ayırt edilir. */
const PRE_RESTORE_TAG = "restore-oncesi";

/* Sıra sabit: kullanıcı hangi adımda olduğunu ve kaç adım kaldığını görsün. */
const ADIMLAR: { id: RestoreStatus; label: string }[] = [
  { id: "bekliyor", label: "Bekliyor" },
  { id: "yedekleniyor", label: "Yedekleniyor" },
  { id: "yukleniyor", label: "Yükleniyor" },
  { id: "tamamlandi", label: "Tamamlandı" },
];

function stamp(iso: string): string {
  return new Date(iso).toLocaleString("tr-TR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/* Sonraki otomatik yedek bir TAHMİN (son yedek + timer aralığı). Tahmin
 * geçmişte kaldıysa "~22:44" yazmak yanıltıcı olur — zamanlayıcı beklendiği
 * gibi çalışmamış demektir, onu söyleriz. */
function sonrakiYedek(iso: string): string {
  const t = new Date(iso);
  if (t.getTime() < Date.now()) return "beklenen zaman geçti";
  return `~${t.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })}`;
}

/** Bilinmeyen boyut "0 B" değil "—" görünür: yalan sayı gösterme. */
function boyut(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

export default function AdminBackups({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [secili, setSecili] = useState<BackupSnapshot | null>(null);

  /* Telegram'a yedek: restic listesinden BAĞIMSIZ (depoya snapshot eklemez),
   * bu yüzden sonucu kendi satırında gösterir, listeyi tazelemez. Şifre
   * sorulmaz — "ana veri yap"ın aksine bu işlem hiçbir şeyin üstüne yazmaz. */
  const [yedekBusy, setYedekBusy] = useState(false);
  const [yedekSonuc, setYedekSonuc] = useState<{ ok: boolean; text: string } | null>(null);

  async function telegramaGonder() {
    setYedekBusy(true);
    setYedekSonuc(null);
    try {
      const r = await adminApi.yedekGonder();
      setYedekSonuc({ ok: true, text: r.message });
    } catch (err) {
      if (err instanceof Unauthorized) {
        onUnauthorized();
        return;
      }
      setYedekSonuc({
        ok: false,
        text: err instanceof Error ? err.message : "Yedek gönderilemedi.",
      });
    } finally {
      setYedekBusy(false);
    }
  }

  const q = useQuery({
    queryKey: ["admin-backups"],
    queryFn: () => adminApi.backups(),
    retry: false,
  });

  /* Süren bir geri yükleme varsa 3 sn'de bir yokla; yoksa boşuna sorma
   * (yanıt yine de son isteğin sonucunu taşır, bir kez okunur). */
  const durum = useQuery({
    queryKey: ["admin-restore-status"],
    queryFn: () => adminApi.restoreStatus(),
    refetchInterval: (query) => (query.state.data?.active ? DURUM_MS : false),
    retry: false,
  });

  useEffect(() => {
    if (q.error instanceof Unauthorized || durum.error instanceof Unauthorized) onUnauthorized();
  }, [q.error, durum.error, onUnauthorized]);

  /* Geri yükleme biter bitmez yedek listesini tazele: güvenlik yedeği
   * ("restore-oncesi") listede görünsün. */
  const oncekiDurum = useRef<RestoreStatus | null>(null);
  const istek = durum.data?.request ?? null;
  const listeyiTazele = q.refetch;
  useEffect(() => {
    const simdiki = istek?.status ?? null;
    if (oncekiDurum.current && oncekiDurum.current !== simdiki && simdiki === "tamamlandi") {
      listeyiTazele();
    }
    oncekiDurum.current = simdiki;
  }, [istek?.status, listeyiTazele]);

  const data = q.data;
  const rows = data?.items ?? [];

  return (
    <div className="admin-backups">
      {data && !data.restore_available && (
        <p className="admin-notice">
          <strong>Geri yükleme kapalı.</strong> İstek kaydı alınmıyor.
        </p>
      )}

      {istek && <RestoreDurum istek={istek} aktif={durum.data?.active ?? false} />}

      <div className="admin-backup-summary">
        <Ozet label="Toplam yedek" value={data ? String(data.total) : "…"} />
        <Ozet label="Son yedek" value={data?.last_time ? stamp(data.last_time) : "—"} />
        <Ozet
          label="Sonraki otomatik yedek"
          value={data?.next_auto_estimate ? sonrakiYedek(data.next_auto_estimate) : "—"}
          hint={
            data ? `sunucudaki zamanlayıcı ${data.auto_interval_minutes} dakikada bir` : undefined
          }
        />
        <Ozet label="Depo" value={data?.repository ?? "—"} wide />
      </div>

      <div className="admin-filters">
        <button className="admin-btn" onClick={() => q.refetch()} disabled={q.isFetching}>
          {q.isFetching ? "Yükleniyor…" : "Yenile"}
        </button>
        <button className="admin-btn" onClick={telegramaGonder} disabled={yedekBusy}>
          {yedekBusy ? "Yedek alınıyor…" : "Şimdi Yedek Al ve Telegram'a Gönder"}
        </button>
      </div>

      {yedekSonuc && (
        <p className={yedekSonuc.ok ? "admin-restore-ok" : "error"}>{yedekSonuc.text}</p>
      )}

      {q.isError && !(q.error instanceof Unauthorized) && (
        <p className="error">{(q.error as Error).message}</p>
      )}

      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th className="admin-col-time">Tarih / saat</th>
              <th>Kimlik</th>
              <th>Boyut</th>
              <th>Konum</th>
              <th aria-label="işlem" />
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.full_id}>
                <td className="admin-col-time admin-mono">
                  {stamp(s.time)}
                  {s.tags.includes(PRE_RESTORE_TAG) && (
                    <span className="admin-badge admin-health-badge-uyari">güvenlik yedeği</span>
                  )}
                </td>
                <td className="admin-mono">{s.id}</td>
                <td className="admin-mono">{boyut(s.size_bytes)}</td>
                <td className="admin-backup-yer">
                  <span className="admin-mono">{s.paths[0] ?? "—"}</span>
                  {s.hostname && <span className="admin-tag">{s.hostname}</span>}
                </td>
                <td className="admin-backup-eylem">
                  <button
                    className="admin-btn"
                    onClick={() => setSecili(s)}
                    disabled={durum.data?.active ?? false}
                    title={durum.data?.active ? "Bir geri yükleme sürüyor" : undefined}
                  >
                    Ana veri yap
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {!q.isLoading && rows.length === 0 && !q.isError && (
          <p className="admin-empty">Depoda henüz yedek yok.</p>
        )}
      </div>

      {secili && (
        <RestoreModal
          snapshot={secili}
          onClose={() => setSecili(null)}
          onUnauthorized={onUnauthorized}
          onDone={() => durum.refetch()}
        />
      )}
    </div>
  );
}

function Ozet({
  label,
  value,
  hint,
  wide,
}: {
  label: string;
  value: string;
  hint?: string;
  wide?: boolean;
}) {
  return (
    <div className={`admin-backup-ozet ${wide ? "admin-backup-ozet-wide" : ""}`}>
      <span className="admin-line-label">{label}</span>
      <strong className="admin-mono">{value}</strong>
      {hint && <span className="muted">{hint}</span>}
    </div>
  );
}

/* Süren/son geri yüklemenin durum şeridi. İlerlemeyi HOST izleyici yazar,
 * panel yalnızca okur — o yüzden buradaki adımlar tahmin değil, sunucudaki
 * gerçek durumdur. */
function RestoreDurum({ istek, aktif }: { istek: RestoreRequest; aktif: boolean }) {
  const hataliMi = istek.status === "hata";
  const bittiMi = istek.status === "tamamlandi";
  const suAn = ADIMLAR.findIndex((a) => a.id === istek.status);

  return (
    <div className={`admin-restore-durum ${hataliMi ? "admin-restore-durum-hata" : ""}`}>
      <div className="admin-restore-durum-bas">
        <strong>
          {hataliMi
            ? "Geri yükleme başarısız"
            : bittiMi
              ? "Geri yükleme tamamlandı"
              : "Geri yükleme sürüyor"}
        </strong>
        <span className="muted admin-mono">
          #{istek.id} · {istek.snapshot_id} · {stamp(istek.requested_at)}
        </span>
      </div>

      {!hataliMi && (
        <ol className="admin-restore-adimlar">
          {ADIMLAR.map((adim, i) => (
            <li
              key={adim.id}
              className={
                i < suAn ? "admin-adim-gecti" : i === suAn ? "admin-adim-simdi" : "admin-adim-sira"
              }
            >
              {adim.label}
            </li>
          ))}
        </ol>
      )}

      {hataliMi && <p className="error">{istek.error_detail ?? "Sebep kaydedilmemiş."}</p>}

      {istek.pre_backup_snapshot && (
        <p className="muted">
          Geri yükleme öncesi güvenlik yedeği:{" "}
          <span className="admin-mono">{istek.pre_backup_snapshot}</span> (listede duruyor)
        </p>
      )}

      {bittiMi && (
        <p className="admin-restore-ok">
          Geri yükleme tamamlandı, sayfayı yenileyin — defter artık bu yedeğin verisini gösteriyor.
        </p>
      )}

      {istek.stale && (
        <p className="admin-notice">
          İstek {stamp(istek.requested_at)}'den beri bekliyor. Sunucudaki geri yükleme izleyicisi
          çalışmıyor olabilir; istek kaybolmadı, izleyici açılınca işlenecek.
        </p>
      )}

      {aktif && !istek.stale && (
        <p className="muted">Durum birkaç saniyede bir güncelleniyor.</p>
      )}
    </div>
  );
}

/* Onay ekranı. Şifre BURADA tekrar sorulur: açık kalmış bir sekme tek tıkla
 * defterin üstüne yazamasın. Şifre doğruluğunu sunucu söyler (403 → Forbidden),
 * yanlış şifre modalı kapatmaz ve oturumu düşürmez. */
function RestoreModal({
  snapshot,
  onClose,
  onUnauthorized,
  onDone,
}: {
  snapshot: BackupSnapshot;
  onClose: () => void;
  onUnauthorized: () => void;
  onDone: () => void;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [hata, setHata] = useState<string | null>(null);
  const [sonuc, setSonuc] = useState<string | null>(null);

  const oncekiAd = `restore öncesi — ${stamp(new Date().toISOString())}`;

  async function onayla() {
    setBusy(true);
    setHata(null);
    try {
      const r = await adminApi.restore(snapshot.id, password);
      setSonuc(r.message);
      setPassword("");
      onDone();
    } catch (err) {
      if (err instanceof Unauthorized) {
        onUnauthorized();
      } else if (err instanceof Forbidden) {
        setHata("Şifre hatalı.");
      } else {
        setHata(err instanceof Error ? err.message : "İşlem yapılamadı.");
      }
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Ana veri yap" onClose={onClose}>
      {sonuc ? (
        <div className="admin-restore">
          <p className="admin-restore-ok">{sonuc}</p>
          <p className="muted">
            Bu pencereyi kapatabilirsin; ilerleme listenin üstündeki durum şeridinde görünür.
          </p>
          <button className="primary" onClick={onClose}>
            Kapat
          </button>
        </div>
      ) : (
        <div className="admin-restore">
          <p className="admin-restore-warn">
            <strong>{stamp(snapshot.time)}</strong> tarihli yedeği ana veritabanı yapmak
            üzeresin. Mevcut veri önce otomatik yedeklenecek (
            <span className="admin-mono">{oncekiAd}</span> adıyla), listede kalacak. Sonra seçili
            yedek yüklenecek. <strong>Bu işlem mevcut verinin üstüne yazar.</strong>
          </p>

          <p className="admin-notice">
            İşi sunucudaki geri yükleme izleyicisi yapar; onayladıktan sonra ilerlemeyi durum
            şeridinden izleyebilirsin.
          </p>

          <dl className="admin-health-details">
            <div className="admin-line">
              <dt className="admin-line-label">Yedek</dt>
              <dd className="admin-line-value admin-mono">{snapshot.id}</dd>
            </div>
            <div className="admin-line">
              <dt className="admin-line-label">Alındığı zaman</dt>
              <dd className="admin-line-value admin-mono">{stamp(snapshot.time)}</dd>
            </div>
            <div className="admin-line">
              <dt className="admin-line-label">Boyut</dt>
              <dd className="admin-line-value admin-mono">{boyut(snapshot.size_bytes)}</dd>
            </div>
          </dl>

          <label className="field">
            <span>Yönetim şifresi</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              autoComplete="current-password"
            />
          </label>

          {hata && <p className="error">{hata}</p>}

          <div className="admin-restore-btns">
            <button className="admin-btn" onClick={onClose} disabled={busy}>
              Vazgeç
            </button>
            <button className="danger" onClick={onayla} disabled={busy || !password}>
              {busy ? "Gönderiliyor…" : "EVET, ANA VERİ YAP"}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
