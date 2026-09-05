/* Koşan format — CLAUDE.md > "Koşan format".
 *
 *   "70-20-50"  =  70 vardı, 20 değişti, 50 oldu.
 *
 * Ayraç (-, +, *) SALT GÖRSELDİR, matematik işareti DEĞİL: "70-20-50",
 * "70+20+50" ve "70*20*50" aynı şeyi anlatır. Format YALNIZCA mal adedi
 * hakkındadır; TL formda kendi alanına ayrıca girilir.
 *
 * Yön ilk ile son sayının karşılaştırmasından çıkar:
 *   ilk > son (azalış) -> BORÇ     ("70-20-50"  -> 20 borç)
 *   ilk < son (artış)  -> TAHSİLAT ("70-30-100" -> 30 tahsilat)
 * Deftere yazılan sayı DEĞİŞİMDİR (fark); ilk ve son yalnızca kullanıcının
 * kendi doğrulaması içindir.
 *
 * Bu dosya app/services/parser.py'deki koşan format mantığının ikizidir
 * (aynı ayraçlar, aynı yanlış-tetiklenme korumaları, aynı matematik
 * kontrolü). Sunucuya gitmeden, kullanıcı yazarken çözülmesi gerektiği için
 * burada da duruyor; kural değişirse İKİSİ birden değişmeli.
 * Testi: web/src/lib/running.test.ts (`just web-test`).
 */

export type RunningKind = "debt" | "payment";

export type Running = {
  before: number;
  change: number;
  after: number;
  /** Deftere yazılacak değişim: |ilk - son|. */
  qty: number;
  kind: RunningKind;
  /** change === qty ? Değilse form kaydetmez, uyarı gösterir. */
  consistent: boolean;
};

/* Parça: baştaki sıfır yok (telefon/kod değil), en çok 6 hane, isteğe bağlı
 * TR ondalık virgülü. Sondaki ileri-bakış DÖRDÜNCÜ bir parçayı tümden
 * reddeder ("0532-456-78-90" hiç eşleşmez). */
const PART = String.raw`(?!0\d)\d{1,6}(?:,\d{1,2})?`;
const SEP = String.raw`[-+*]`;
const RUNNING_RE = new RegExp(
  `^\\s*(${PART})\\s*${SEP}\\s*(${PART})\\s*${SEP}\\s*(${PART})\\s*$`,
);

function toNumber(part: string): number {
  return Number(part.replace(",", "."));
}

/** gg-aa-yyyy / gg-aa-yy / yyyy-aa-gg biçimleri. Yalnızca matematik
 *  TUTMADIĞINDA elemek için kullanılır: tutarlı bir üçlü ("10-5-5") geçerli
 *  bir koşan formattır, tarihe benzemesi onu bozmaz. */
function looksLikeDate(before: number, change: number, after: number): boolean {
  if (![before, change, after].every(Number.isInteger)) return false;
  if (before >= 1 && before <= 31 && change >= 1 && change <= 12 && (after <= 99 || (after >= 1900 && after <= 2199))) {
    return true;
  }
  return before >= 1900 && before <= 2199 && change >= 1 && change <= 12 && after >= 1 && after <= 31;
}

/** Adet alanına yazılan metni koşan format olarak çözer. Üçlü değilse,
 *  tarih biçimindeyse ya da hareket sıfırsa null döner — alan o zaman
 *  eskisi gibi düz bir sayı olarak okunur. */
export function parseRunning(input: string): Running | null {
  const m = RUNNING_RE.exec(input ?? "");
  if (!m) return null;

  const before = toNumber(m[1]);
  const change = toNumber(m[2]);
  const after = toNumber(m[3]);
  if (![before, change, after].every(Number.isFinite)) return null;

  const qty = Math.abs(before - after);
  if (qty === 0) return null; // "70-0-70": bir hareket yok

  const consistent = change === qty;
  if (!consistent && looksLikeDate(before, change, after)) return null;

  return { before, change, after, qty, kind: before > after ? "debt" : "payment", consistent };
}

function say(value: number): string {
  return String(value).replace(".", ",");
}

/** Matematik tutmuyorsa gösterilecek hata metni; tutuyorsa null. */
export function runningError(running: Running): string | null {
  if (running.consistent) return null;
  return (
    `Sayılar tutmuyor: ${say(running.before)} → ${say(running.after)} için fark ` +
    `${say(running.qty)} olmalı ama ${say(running.change)} yazdınız.`
  );
}

/** Doğru bir üçlünün özeti: "70 → 50 · 20 balya (borç)". */
export function runningHint(running: Running, unit: string): string {
  const birim = unit.trim() ? ` ${unit.trim()}` : "";
  const yon = running.kind === "debt" ? "borç" : "tahsilat";
  return `${say(running.before)} → ${say(running.after)} · ${say(running.qty)}${birim} (${yon})`;
}

/** Kullanıcının açtığı modal ile sayıların söylediği yön çelişiyorsa uyarı.
 *  Engellemez: modalı kullanıcı bilerek seçti, ama fark etmesi gerekir. */
export function runningDirectionWarning(running: Running, formKind: RunningKind): string | null {
  if (running.kind === formKind) return null;
  return running.kind === "payment"
    ? `Sayılar artıyor (${say(running.before)} → ${say(running.after)}), bu bir tahsilata benziyor.`
    : `Sayılar azalıyor (${say(running.before)} → ${say(running.after)}), bu bir borca benziyor.`;
}
