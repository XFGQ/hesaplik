const tl = new Intl.NumberFormat("tr-TR", {
  style: "currency",
  currency: "TRY",
  minimumFractionDigits: 2,
});

const qtyFmt = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 3 });

/** Defterde tutar her zaman pozitif yazılır; yön renkle ve etiketle verilir. */
export function money(value: string | number): string {
  return tl.format(Math.abs(Number(value)));
}

export function qty(value: string | number): string {
  return qtyFmt.format(Number(value));
}

export function balanceTone(value: string | number): "borc" | "tahsilat" | "zero" {
  const n = Number(value);
  if (n > 0) return "borc";
  if (n < 0) return "tahsilat";
  return "zero";
}

export function balanceLabel(value: string | number): string {
  const n = Number(value);
  if (n > 0) return "alacağımız";
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
