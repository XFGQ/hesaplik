/* Sesli mesaj kaydının SAF kısmı: süre biçimi, mikrofon seviyesinden dalga
 * çubukları, tarayıcının ürettiği biçim → dosya adı. Tarayıcı API'lerine
 * (MediaRecorder/AudioContext) dokunmaz ki DOM'suz test edilebilsin
 * (web/src/lib/voiceWave.test.ts, `just web-test`). Plumbing tarafı
 * web/src/lib/useVoiceRecorder.ts'te. */

/** Kayıt çubuğundaki dalga çubuğu sayısı — CSS'te sabit genişlik varsaymaz. */
export const WAVE_BARS = 28;

/** Çubuğun en kısa hâli (yüzde): sessizlikte de bir çizgi görünsün, kayıt
 *  sürüyor belli olsun. */
export const WAVE_MIN = 8;

/** Kayıt üst sınırı. WhatsApp gibi kendiliğinden durur: telefon cebe girip
 *  saatlerce kayıt yapmasın, Groq'a devasa dosya gitmesin. */
export const MAX_RECORD_SECONDS = 120;

/** Bu kadar kısa kayıt kazara dokunuştur; gönderilmez. */
export const MIN_RECORD_SECONDS = 1;

/** "0:07", "1:23" — sayaç dakikayı da göstersin, uzun kayıtta "83" okunmaz. */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${mm}:${String(ss).padStart(2, "0")}`;
}

/* AnalyserNode'un zaman-alanı örnekleri 0..255 arası, sessizlik 128
 * (merkez). RMS alınır (tek tepe değil ortalama enerji: konuşma sürerken
 * çubuklar titremesin), sonra 0..1'e sıkıştırılır. Çarpan deneyle seçildi:
 * normal konuşma mesafesinde dalga tepeye değsin ama sürekli doymasın. */
export function levelFromTimeDomain(samples: Uint8Array | number[]): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    const v = (samples[i] - 128) / 128;
    sum += v * v;
  }
  const rms = Math.sqrt(sum / samples.length);
  return Math.min(1, rms * 3.2);
}

/* Dalga SOLA kayar (WhatsApp gibi): yeni seviye sağdan girer, en eskisi
 * düşer. Böylece kullanıcı yalnızca "ses var mı"yı değil, biraz önce ne
 * söylediğini de görür. */
export function pushWaveLevel(bars: number[], level: number): number[] {
  const height = WAVE_MIN + Math.round(Math.min(1, Math.max(0, level)) * (100 - WAVE_MIN));
  return [...bars.slice(1), height];
}

export function emptyWave(): number[] {
  return new Array(WAVE_BARS).fill(WAVE_MIN);
}

/* Tarayıcılar farklı biçim üretir: Chrome/Firefox webm/opus, iOS Safari
 * mp4/aac. Groq ikisini de kabul eder ama biçimi DOSYA ADININ uzantısından
 * anlar — bu yüzden uzantı MediaRecorder'ın gerçek mimeType'ından türetilir,
 * varsayılmaz. Sunucu ayrıca kendi allowlist'ini uygular
 * (app/api/chat.py > _voice_filename). */
const MIME_EXTENSIONS: [string, string][] = [
  ["audio/webm", "webm"],
  ["audio/ogg", "ogg"],
  ["audio/mp4", "mp4"],
  ["audio/mpeg", "mp3"],
  ["audio/wav", "wav"],
  ["audio/x-m4a", "m4a"],
];

export function filenameForMime(mime: string): string {
  const base = (mime || "").split(";")[0].trim().toLowerCase();
  const hit = MIME_EXTENSIONS.find(([m]) => m === base);
  return `voice.${hit ? hit[1] : "webm"}`;
}

/* Tercih sırası: opus'lu webm (küçük ve Groq'un en sevdiği), sonra düz webm,
 * ogg, en sonda Safari'nin mp4'ü. Hiçbiri desteklenmiyorsa boş string döner
 * ve MediaRecorder'a mimeType verilmez — tarayıcı kendi varsayılanını
 * kullanır, gerçek biçim yine `recorder.mimeType`tan okunur. */
export const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
];

export function pickMimeType(isSupported: (mime: string) => boolean): string {
  return MIME_CANDIDATES.find((m) => isSupported(m)) ?? "";
}
