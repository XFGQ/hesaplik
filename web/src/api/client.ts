import { authHeader, onUnauthorized } from "../lib/auth";
import type {
  AdminLLMStatus,
  AdminVllmControl,
  Balance,
  BackupRunResult,
  BackupSnapshot,
  EntryInput,
  EntryResult,
  Person,
  PersonInput,
  PersonRow,
  Product,
  Settings,
  TxDetail,
} from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "";

export class ApiError extends Error {}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeader(),
      ...(init?.headers as Record<string, string> | undefined),
    },
  });
  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError("Oturum geçersiz");
  }
  if (!res.ok) {
    let detail = `İstek başarısız (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail) && body.detail[0]?.msg) detail = body.detail[0].msg;
    } catch {
      /* gövde JSON değilse varsayılan mesaj kalır */
    }
    throw new ApiError(detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

/* PDF raporlar: doğrudan API URL'sine <a>/window.open ile gidilemez —
 * tarayıcı navigasyonu Authorization header'ı taşıyamaz. Bunun yerine
 * fetch ile (header'lı) blob olarak indirilir, sonra `download` attribute'lu
 * gizli bir <a> tıklanır — tarayıcı dosyayı indirir (bkz. CLAUDE.md > Faz
 * 4b "tıkla → PDF indir"). Dosya adı sunucunun Content-Disposition
 * başlığından okunur (tek doğru kaynak backend'de kalır). */
async function openReport(path: string): Promise<void> {
  const res = await fetch(`${BASE}/api${path}`, { headers: authHeader() });
  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError("Oturum geçersiz");
  }
  if (!res.ok) throw new ApiError(`Rapor alınamadı (${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const filename = /filename="?([^"]+)"?/.exec(res.headers.get("Content-Disposition") ?? "")?.[1];
  const a = document.createElement("a");
  a.href = url;
  a.download = filename ?? "rapor.pdf";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export type LoginResult = { access_token: string; token_type: string; expires_in: number };

/* Girişin kendi fetch'i: `req()`'ün 401'i "oturum düştü, girişe dön" diye
 * yorumlayan ortak mantığından KASTEN ayrı — burada 401 zaten girişteyken
 * "kullanıcı adı/şifre hatalı" demektir, backend'in asıl mesajı olduğu
 * gibi kullanıcıya gösterilmeli. */
export async function login(username: string, password: string): Promise<LoginResult> {
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    let detail = res.status === 401 ? "Kullanıcı adı veya şifre hatalı" : `Giriş başarısız (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* gövde JSON değilse varsayılan mesaj kalır */
    }
    throw new ApiError(detail);
  }
  return res.json();
}

export const api = {
  persons: (q?: string) =>
    req<PersonRow[]>(`/persons${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  person: (id: number) => req<Person>(`/persons/${id}`),
  createPerson: (body: PersonInput) =>
    req<Person>("/persons", { method: "POST", body: JSON.stringify(body) }),
  updatePerson: (id: number, body: PersonInput) =>
    req<Person>(`/persons/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deletePerson: (id: number) => req<void>(`/persons/${id}`, { method: "DELETE" }),

  balance: (id: number) => req<Balance>(`/persons/${id}/balance`),
  transactions: (id: number) => req<TxDetail[]>(`/persons/${id}/transactions`),
  products: () => req<Product[]>("/products"),

  addDebt: (body: EntryInput) =>
    req<EntryResult>("/debts", { method: "POST", body: JSON.stringify(body) }),
  addPayment: (body: EntryInput) =>
    req<EntryResult>("/payments", { method: "POST", body: JSON.stringify(body) }),
  reverse: (tx_id: number, reason: string) =>
    req<{ id: number }>(`/transactions/${tx_id}/reverse`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  deleteTransaction: (tx_id: number, reason: string) =>
    req<void>(`/transactions/${tx_id}`, {
      method: "DELETE",
      body: JSON.stringify({ reason }),
    }),

  getSettings: () => req<Settings>("/settings"),
  updateSetting: (key: string, value: string) =>
    req<{ key: string; value: string }>(`/settings/${encodeURIComponent(key)}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),

  getBackups: () => req<BackupSnapshot[]>("/backups"),
  runBackup: () => req<BackupRunResult>("/backups/run", { method: "POST" }),

  openDailyReport: (date?: string) =>
    openReport(`/reports/daily${date ? `?date=${encodeURIComponent(date)}` : ""}`),
  openGeneralReport: () => openReport("/reports/general"),
  openPersonReport: (id: number) => openReport(`/reports/person/${id}`),

  adminLlmStatus: () => req<AdminLLMStatus>("/admin/llm"),
  adminSetLlmPrimary: (llmPrimary: string) =>
    req<AdminLLMStatus>("/admin/llm", {
      method: "POST",
      body: JSON.stringify({ llm_primary: llmPrimary }),
    }),

  adminVllmControl: () => req<AdminVllmControl>("/admin/vllm-control"),
  adminSetVllmDesired: (desired: "on" | "off") =>
    req<AdminVllmControl>("/admin/vllm-control", {
      method: "POST",
      body: JSON.stringify({ desired }),
    }),
};
