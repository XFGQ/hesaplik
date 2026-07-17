export type PersonWithBalance = {
  id: number;
  full_name: string;
  phone: string | null;
  balance_try: string;
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
  items: { product_name: string; qty: string; unit: string }[];
};
