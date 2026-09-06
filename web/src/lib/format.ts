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

/** Kişi defterinin ÜST bilgisindeki güncel durum rengi: borçlu kırmızı,
 *  lehte (fazla ödeme) yeşil. Hareket tutarlarındaki borç-kırmızı /
 *  tahsilat-mavi ayrımından ayrı tutulur: orada tek bir işlemin TÜRÜ,
 *  burada hesabın genel DURUMU anlatılır. */
export function accountTone(value: string | number): "borc" | "alacak" | "zero" {
  const n = Number(value);
  if (n > 0) return "borc";
  if (n < 0) return "alacak";
  return "zero";
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

// ---------------------------------------------------------- hareket kartı
//
// Kişi defterindeki hareketler tablo değil kart olarak çizilir (PersonDetail).
// Karttaki dört parça — dikey tarih, başlık, tür etiketi, birim fiyat —
// burada üretilir: saf metin, DOM'suz test edilebilir (format.test.ts).

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric" });
const monthYearFmt = new Intl.DateTimeFormat("tr-TR", { month: "short", year: "2-digit" });

/** Kartın sol ucundaki dikey tarih: gün büyük ("28"), altında ay-yıl
 *  ("Tem 25"), en altta saat ("16:07"). Saat hhmm ile YEREL saatten çıkar —
 *  backend UTC saklar, defterde okunması gereken kullanıcının saatidir.
 *  Sıfır dolgulu (09:05), "9:5" gibi bir şey asla çıkmaz. */
export function dateParts(iso: string): { day: string; monthYear: string; time: string } {
  const d = new Date(iso);
  return { day: dayFmt.format(d), monthYear: monthYearFmt.format(d), time: hhmm(d) };
}

type TxLike = {
  kind: "DEBIT" | "CREDIT";
  note: string | null;
  reverses_id?: number | null;
  is_reversed?: boolean;
  lines: { product_name: string; qty: string; unit: string; unit_price: string }[];
};

/** Kartın başlığı = işlemin İÇERİĞİ: "20 balya saman". Kalem yoksa not,
 *  o da yoksa işlemin kendisi ("Nakit borç" / "Nakit tahsilat"). */
export function txCardTitle(t: TxLike): string {
  if (t.lines.length > 0) {
    const l = t.lines[0];
    const bas = `${qty(l.qty)} ${l.unit} ${l.product_name.toLocaleLowerCase("tr")}`;
    return t.lines.length > 1 ? `${bas} +${t.lines.length - 1}` : bas;
  }
  if (t.note) return t.note;
  return t.kind === "DEBIT" ? "Nakit borç" : "Nakit tahsilat";
}

/** Alt satırın solundaki tür etiketi. İptal durumu türü EZER: kullanıcı
 *  önce "bu kayıt artık geçerli mi?" sorusunun cevabını görmeli. */
export function txKindLabel(t: TxLike): string {
  if (t.is_reversed) return "İptal edildi";
  if (t.reverses_id) return "İptal kaydı";
  if (t.kind === "DEBIT") return "Borç eklendi";
  return t.lines.length > 0 ? "Tahsilat yapıldı" : "Nakit tahsilat yapıldı";
}

/** "75,00 ₺/balya" — kalemi olmayan (düz para) kayıtta gösterilecek bir
 *  birim fiyat yoktur, null döner. */
export function txUnitPrice(t: TxLike): string | null {
  if (t.lines.length === 0) return null;
  const l = t.lines[0];
  return `${money(l.unit_price)}/${l.unit}`;
}
