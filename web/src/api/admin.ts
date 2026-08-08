/* Admin paneli API istemcisi.
 *
 * Oturum httpOnly çerezle taşınır: token JS'te hiç tutulmaz (localStorage
 * yok — XSS ile çalınacak bir şey de yok). Bu yüzden her istek
 * `credentials: "include"` ile gider; sunucu çerezi kendisi okur.
 *
 * 401 ayrı bir hata tipiyle işaretlenir (Unauthorized): panel bunu görünce
 * şifre ekranına düşer, "istek başarısız" diye anlamsız bir hata göstermez.
 */

const BASE = import.meta.env.VITE_API_URL ?? "";

export class AdminError extends Error {}
export class Unauthorized extends AdminError {}
/** 403: oturum geçerli ama bu işleme izin yok (ör. geri yüklemede yanlış
 * şifre). Unauthorized'dan ayrı tutulur — panel bunu görünce şifre ekranına
 * DÜŞMEZ, hatayı olduğu yerde gösterir. */
export class Forbidden extends AdminError {}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}/api/admin${path}`, {
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    ...init,
  });

  if (res.status === 401) throw new Unauthorized("Oturum geçersiz");
  if (!res.ok) {
    let detail = `İstek başarısız (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* gövde JSON değilse varsayılan mesaj kalır */
    }
    throw res.status === 403 ? new Forbidden(detail) : new AdminError(detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export type FlowTx = {
  id: number;
  kind: "DEBIT" | "CREDIT";
  amount_try: string;
  person_id: number;
  person_name: string;
  occurred_at: string;
};

export type FlowRow = {
  id: number;
  received_at: string;
  processed_at: string | null;
  channel: string;
  chat_id: string | null;
  text: string | null;
  detected_kind: string | null;
  detected_person: string | null;
  detected_amount: string | null;
  detected_product: string | null;
  detected_qty: string | null;
  detected_unit: string | null;
  parse_source: string | null;
  parse_ms: number | null;
  outcome: string | null;
  outcome_detail: string | null;
  transaction: FlowTx | null;
};

export type FlowPage = {
  total: number;
  limit: number;
  offset: number;
  items: FlowRow[];
};

/* Sistem sağlığı. status: ok | uyari | hata | bilgi — "bilgi" ne iyi ne
 * kötü (kapalı LLM, sessiz bot); genel özeti bozmaz.
 * measured=false: değer doğrudan ölçülmedi, dolaylı bir izden çıkarıldı. */
export type HealthDetail = { label: string; value: string };

export type HealthComponent = {
  id: string;
  label: string;
  status: "ok" | "uyari" | "hata" | "bilgi";
  summary: string;
  details: HealthDetail[];
  measured: boolean;
  note: string | null;
};

export type Health = {
  checked_at: string;
  overall: "ok" | "uyari" | "hata";
  overall_text: string;
  components: HealthComponent[];
};

/* Yedekleme. size_bytes null olabilir: restic her zaman özet vermez,
 * bilinmeyen boyut 0 diye gösterilmez. next_auto_estimate bir TAHMİNDİR —
 * zamanlayıcı host'ta, container içinden ölçülemez. */
export type BackupSnapshot = {
  id: string;
  full_id: string;
  time: string;
  size_bytes: number | null;
  hostname: string | null;
  tags: string[];
  paths: string[];
};

export type BackupList = {
  repository: string;
  total: number;
  last_time: string | null;
  auto_interval_minutes: number;
  next_auto_estimate: string | null;
  restore_available: boolean;
  items: BackupSnapshot[];
};

/* Geri yükleme isteği. Durumu yazan taraf HOST izleyicidir (systemd);
 * API yalnızca isteği kuyruğa koyar ve durumu okur.
 * stale=true: uzun süredir "bekliyor" — izleyici çalışmıyor olabilir. */
export type RestoreStatus =
  | "bekliyor"
  | "yedekleniyor"
  | "yukleniyor"
  | "tamamlandi"
  | "hata";

export type RestoreRequest = {
  id: number;
  snapshot_id: string;
  status: RestoreStatus;
  requested_at: string;
  requested_by: string;
  pre_backup_snapshot: string | null;
  started_at: string | null;
  finished_at: string | null;
  error_detail: string | null;
  stale: boolean;
};

export type RestoreStart = {
  ok: boolean;
  message: string;
  request: RestoreRequest;
};

export type RestoreStatusResponse = {
  active: boolean;
  request: RestoreRequest | null;
};

export type FlowFilters = {
  kind?: string;
  person?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
};

export const adminApi = {
  login: (password: string) =>
    req<{ ok: boolean; expires_in: number }>("/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  logout: () => req<{ ok: boolean }>("/logout", { method: "POST" }),
  me: () => req<{ ok: boolean }>("/me"),
  health: () => req<Health>("/health"),

  backups: () => req<BackupList>("/backups"),
  /** "Ana veri yap" İSTEĞİ. Şifre gövdede tekrar sorulur (yanlışsa Forbidden);
   * zaten süren bir geri yükleme varsa 409 → AdminError. */
  restore: (snapshotId: string, password: string) =>
    req<RestoreStart>("/backups/restore", {
      method: "POST",
      body: JSON.stringify({ snapshot_id: snapshotId, password }),
    }),
  restoreStatus: () => req<RestoreStatusResponse>("/backups/restore/status"),

  flow: (f: FlowFilters) => {
    const q = new URLSearchParams();
    if (f.kind) q.set("kind", f.kind);
    if (f.person) q.set("person", f.person);
    if (f.from) q.set("from", f.from);
    if (f.to) q.set("to", f.to);
    q.set("limit", String(f.limit ?? 50));
    q.set("offset", String(f.offset ?? 0));
    return req<FlowPage>(`/flow?${q.toString()}`);
  },
};
