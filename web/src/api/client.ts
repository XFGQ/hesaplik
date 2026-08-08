import type {
  AdminLLMStatus,
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
      ...(init?.headers as Record<string, string> | undefined),
    },
  });
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

  dailyReportUrl: (date?: string) =>
    `${BASE}/api/reports/daily${date ? `?date=${encodeURIComponent(date)}` : ""}`,
  generalReportUrl: () => `${BASE}/api/reports/general`,
  personReportUrl: (id: number) => `${BASE}/api/reports/person/${id}`,

  adminLlmStatus: (password: string) =>
    req<AdminLLMStatus>("/admin/llm", { headers: { "X-Admin-Password": password } }),
  adminSetLlmPrimary: (password: string, llmPrimary: string) =>
    req<AdminLLMStatus>("/admin/llm", {
      method: "POST",
      headers: { "X-Admin-Password": password },
      body: JSON.stringify({ llm_primary: llmPrimary }),
    }),
};
