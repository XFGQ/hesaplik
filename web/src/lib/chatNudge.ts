/* Sohbet balonunun (FAB) "buradayım" hatırlatması ve panelin dışına tıklayınca
 * küçülme kuralı. Zamanlayıcı ve karar mantığı bileşenden ayrı durur ki
 * React/DOM olmadan test edilebilsin (web/src/lib/chatNudge.test.ts). */

/** İlk hatırlatma sayfa açılışından bu kadar sonra (kullanıcı yerleşsin). */
export const NUDGE_INTRO_DELAY_MS = 2500;
/** İki hatırlatmanın BAŞLANGICI arası — "~10 saniyede bir". */
export const NUDGE_PERIOD_MS = 10_000;
/** Bir hatırlatmanın süresi; CSS'teki animasyon süresiyle aynı olmalı. */
export const NUDGE_DURATION_MS = 1_400;

/** "intro" ilk seferlik, biraz daha belirgin; "soft" periyodik hafif hareket. */
export type NudgeKind = "none" | "intro" | "soft";

type Containable = { contains: (node: Node) => boolean };

/* Panel açıkken boşluğa tıklamak paneli küçültür. Karar tek satırlık ama
 * kuralın kendisi önemli: panelin İÇİ (girdi, butonlar, mesajlar) asla
 * küçültmez, hedef bilinemiyorsa (panel henüz yok / hedef DOM düğümü değil)
 * hiçbir şey yapılmaz — belirsizlikte sohbeti kapatmayız. */
export function isOutsideClick(panel: Containable | null, target: EventTarget | null): boolean {
  if (!panel || target === null) return false;
  return !panel.contains(target as Node);
}

/* Panel KAPALIYKEN çalışan döngü: bekle → kısa animasyon → "none" → bekle...
 * `emit` her aşamada çağrılır, dönen fonksiyon döngüyü durdurur (panel
 * açılınca temizlenir; açıkken hatırlatma yapılmaz, rahatsız eder). */
export function startNudgeCycle(
  emit: (kind: NudgeKind) => void,
  opts: { intro: boolean } = { intro: false },
): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let intro = opts.intro;
  const gap = Math.max(0, NUDGE_PERIOD_MS - NUDGE_DURATION_MS);

  const schedule = (delay: number) => {
    timer = setTimeout(() => {
      emit(intro ? "intro" : "soft");
      intro = false;
      timer = setTimeout(() => {
        emit("none");
        schedule(gap);
      }, NUDGE_DURATION_MS);
    }, delay);
  };

  schedule(opts.intro ? NUDGE_INTRO_DELAY_MS : gap);
  return () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };
}
