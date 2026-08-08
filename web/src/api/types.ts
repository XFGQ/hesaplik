export type Person = {
  id: number;
  full_name: string;
  phone: string | null;
  city: string | null;
  district: string | null;
  address: string | null;
  note: string | null;
};

export type Item = { product_name: string; qty: string; unit: string };

export type PersonRow = Person & {
  balance_try: string;
  items: Item[];
  last_activity: string | null;
};

export type Product = {
  id: number;
  name: string;
  base_unit: string;
  unit_price: string | null;
};

export type TxLine = {
  product_name: string;
  qty: string;
  unit: string;
  unit_price: string;
  line_total: string;
};

export type TxDetail = {
  id: number;
  person_id: number;
  kind: "DEBIT" | "CREDIT";
  amount_try: string;
  occurred_at: string;
  status: string;
  source: string;
  note: string | null;
  reverses_id: number | null;
  is_reversed: boolean;
  lines: TxLine[];
};

export type Balance = {
  person_id: number;
  balance_try: string;
  is_receivable: boolean;
  items: Item[];
};

export type PersonInput = {
  full_name: string;
  phone?: string | null;
  city?: string | null;
  district?: string | null;
  address?: string | null;
  note?: string | null;
};

export type EntryInput = {
  person_id: number;
  product_name?: string | null;
  qty?: string | null;
  unit?: string | null;
  amount: string;
  occurred_at?: string | null;
  note?: string | null;
};

export type EntryResult = {
  id: number;
  product_name: string | null;
  product_created: boolean;
};

export type Settings = Record<string, string>;

export type AdminLLMSource = {
  ok: boolean;
  url: string;
  model: string;
};

export type AdminLLMStatus = {
  primary: "auto" | "vllm" | "ollama" | "none";
  active: "vllm" | "ollama" | "none";
  vllm: AdminLLMSource;
  ollama: AdminLLMSource;
};

export type BackupSnapshot = {
  id: string;
  time: string;
  size_bytes: number;
};

export type BackupRunResult = {
  ok: boolean;
  message: string;
  duration_seconds: number;
};
