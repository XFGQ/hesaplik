const BASE = import.meta.env.VITE_API_URL ?? "";

export class ApiError extends Error {}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = `İstek başarısız (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* gövde JSON değilse varsayılan mesaj kalır */
    }
    throw new ApiError(detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  persons: (q?: string) =>
    req<import("./types").PersonWithBalance[]>(
      `/persons${q ? `?q=${encodeURIComponent(q)}` : ""}`,
    ),
  createPerson: (full_name: string, phone?: string) =>
    req<{ id: number }>("/persons", {
      method: "POST",
      body: JSON.stringify({ full_name, phone: phone || null }),
    }),
  balance: (id: number) => req<import("./types").Balance>(`/persons/${id}/balance`),
  transactions: (id: number) => req<import("./types").TxDetail[]>(`/persons/${id}/transactions`),
  products: () => req<import("./types").Product[]>("/products"),
  addDebt: (person_id: number, product_id: number, qty: string) =>
    req<{ id: number }>("/debts", {
      method: "POST",
      body: JSON.stringify({ person_id, lines: [{ product_id, qty }] }),
    }),
  addPayment: (person_id: number, amount: string) =>
    req<{ id: number }>("/payments", {
      method: "POST",
      body: JSON.stringify({ person_id, amount }),
    }),
  reverse: (tx_id: number, reason: string) =>
    req<{ id: number }>(`/transactions/${tx_id}/reverse`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
};
