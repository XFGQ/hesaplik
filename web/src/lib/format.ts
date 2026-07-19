const tl = new Intl.NumberFormat("tr-TR", {
  style: "currency",
  currency: "TRY",
  minimumFractionDigits: 2,
});

const qtyFmt = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 3 });

export function money(value: string | number): string {
  return tl.format(Math.abs(Number(value)));
}

export function qty(value: string | number): string {
  return qtyFmt.format(Math.abs(Number(value)));
}

export function balanceTone(value: string | number): "borc" | "tahsilat" | "zero" {
  const n = Number(value);
  if (n > 0) return "borc";
  if (n < 0) return "tahsilat";
  return "zero";
}

/** + işaretli = borçlu, − = tahsilatlı/fazla ödeme. Kullanıcı + / − görmek istiyor. */
export function signedMoney(value: string | number): string {
  const n = Number(value);
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  return `${sign}${money(n)}`;
}

export function balanceLabel(value: string | number): string {
  const n = Number(value);
  if (n > 0) return "borçlu";
  if (n < 0) return "fazla ödeme";
  return "hesap kapalı";
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString("tr-TR", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Mal kalemleri: + kişi almış (borçlu), − peşin ödemiş (kişi alacaklı). */
export function itemLabel(item: { product_name: string; qty: string; unit: string }): string {
  const n = Number(item.qty);
  const name = item.product_name.toLocaleLowerCase("tr");
  const q = qty(item.qty);
  if (n > 0) return `${q} ${item.unit} ${name} borçlu`;
  return `${q} ${item.unit} ${name} alacaklı`;
}

export function itemsSummary(items: { product_name: string; qty: string; unit: string }[]): string {
  return items.map(itemLabel).join(" · ");
}

export function parseNumber(input: string): string {
  return input.replace(/\./g, "").replace(",", ".").trim();
}

/** parseNumber'ın tersi: backend'den gelen "1500.00" gibi ham ondalık
 * metni, kullanıcının düzenleyebileceği "1500,00" gösterime çevirir. */
export function toEditableNumber(raw: string): string {
  return raw.replace(".", ",");
}

export function hhmm(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}
