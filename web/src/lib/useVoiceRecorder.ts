/* Mikrofon kaydının tarayıcı tarafı: izin (getUserMedia), kayıt
 * (MediaRecorder), canlı seviye (Web Audio AnalyserNode). Saf mantık ayrı
 * durur (web/src/lib/voiceWave.ts) ve orada test edilir; burada yalnızca
 * tarayıcı API'lerinin bağlanması ve temizliği var.
 *
 * Sözleşme kasten söz/callback değil, imperatif: `start()` izin ister,
 * `stop()` kaydı bitirip Blob'a çözülür, `cancel()` her şeyi atar. Böylece
 * bileşen "gönder"i tek bir `await` ile yazar, kapanmış closure'lardan gelen
 * bayat state derdi olmaz.
 *
 * Kaynak sızıntısı en büyük risk: mikrofon açık kalırsa telefonda kayıt
 * ışığı yanar durur. Bu yüzden temizlik TEK yerde (`teardown`) toplanır ve
 * her yol (gönder, iptal, hata, bileşen sökümü) oradan geçer. */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  emptyWave,
  filenameForMime,
  levelFromTimeDomain,
  pickMimeType,
  pushWaveLevel,
} from "./voiceWave";

export type RecorderState = "idle" | "requesting" | "recording";

export type VoiceRecording = { blob: Blob; filename: string; seconds: number };

/** İzin reddi ile "mikrofon yok/başka uygulama kullanıyor" ayrı mesaj hak
 *  eder: ilki kullanıcının kendi kararı, ikincisi cihaz sorunu. */
export type RecorderError = "denied" | "unavailable";

const MIC_CONSTRAINTS: MediaStreamConstraints = {
  audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
};

export function useVoiceRecorder() {
  const [state, setState] = useState<RecorderState>("idle");
  const [seconds, setSeconds] = useState(0);
  const [bars, setBars] = useState<number[]>(emptyWave);

  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedAtRef = useRef(0);
  /* stop() çağrısını MediaRecorder'ın "stop" olayına bağlar: veri ancak o
   * olaydan sonra tamamdır. cancel() aynı resolver'ı null ile kapatır. */
  const resolveRef = useRef<((rec: VoiceRecording | null) => void) | null>(null);

  const teardown = useCallback(() => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    if (tickRef.current !== null) clearInterval(tickRef.current);
    tickRef.current = null;
    audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    recorderRef.current = null;
    chunksRef.current = [];
    setState("idle");
    setSeconds(0);
    setBars(emptyWave());
  }, []);

  /* Bileşen sökülürse (sayfa değişimi, oturum düşmesi) mikrofon kapanmalı. */
  useEffect(() => teardown, [teardown]);

  const start = useCallback(async (): Promise<RecorderError | null> => {
    if (recorderRef.current) return null;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      return "unavailable";
    }
    setState("requesting");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
    } catch (e) {
      setState("idle");
      const name = (e as DOMException)?.name;
      return name === "NotAllowedError" || name === "SecurityError" ? "denied" : "unavailable";
    }

    streamRef.current = stream;
    const mime = pickMimeType((m) => MediaRecorder.isTypeSupported(m));
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    recorderRef.current = recorder;
    chunksRef.current = [];

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };
    recorder.onstop = () => {
      const resolve = resolveRef.current;
      resolveRef.current = null;
      /* Gerçek biçim recorder'dan okunur: istediğimiz mime desteklenmemiş
       * olabilir (iOS), uzantı ona göre seçilir. */
      const actual = recorder.mimeType || mime || "audio/webm";
      const blob = new Blob(chunksRef.current, { type: actual });
      const elapsed = (Date.now() - startedAtRef.current) / 1000;
      teardown();
      resolve?.(blob.size > 0 ? { blob, filename: filenameForMime(actual), seconds: elapsed } : null);
    };

    /* Canlı seviye: kaydın kendisinden bağımsız bir dinleme hattı — analiz
     * durursa kayıt yine sürer (dalga bir süs, veri değil). */
    try {
      const ctx = new AudioContext();
      audioCtxRef.current = ctx;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const buffer = new Uint8Array(analyser.fftSize);
      const loop = () => {
        analyser.getByteTimeDomainData(buffer);
        setBars((prev) => pushWaveLevel(prev, levelFromTimeDomain(buffer)));
        rafRef.current = requestAnimationFrame(loop);
      };
      rafRef.current = requestAnimationFrame(loop);
    } catch {
      /* AudioContext kurulamadıysa (nadiren, izin/otomatik oynatma kuralları)
       * dalga düz kalır; kayıt etkilenmez. */
    }

    startedAtRef.current = Date.now();
    setSeconds(0);
    tickRef.current = setInterval(
      () => setSeconds(Math.floor((Date.now() - startedAtRef.current) / 1000)),
      250,
    );
    recorder.start();
    setState("recording");
    return null;
  }, [teardown]);

  const stop = useCallback((): Promise<VoiceRecording | null> => {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") {
      teardown();
      return Promise.resolve(null);
    }
    return new Promise<VoiceRecording | null>((resolve) => {
      resolveRef.current = resolve;
      recorder.stop();
    });
  }, [teardown]);

  const cancel = useCallback(() => {
    const recorder = recorderRef.current;
    resolveRef.current = null;
    if (recorder && recorder.state !== "inactive") {
      recorder.onstop = null;
      recorder.stop();
    }
    teardown();
  }, [teardown]);

  return { state, seconds, bars, start, stop, cancel };
}