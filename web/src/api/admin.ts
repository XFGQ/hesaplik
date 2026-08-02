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
    throw new AdminError(detail);
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
