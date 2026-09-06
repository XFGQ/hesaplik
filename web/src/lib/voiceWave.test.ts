/* Bağımlılıksız çalışır:  node --test web/src/lib/voiceWave.test.ts
 * (bkz. Justfile > web-test). Sesli mesajın SAF kısmı burada test edilir;
 * MediaRecorder/getUserMedia tarafı (useVoiceRecorder.ts) tarayıcı API'si
 * olduğu için DOM'suz test edilemez, bu yüzden mantık oradan ayrıldı. */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  MAX_RECORD_SECONDS,
  MIN_RECORD_SECONDS,
  WAVE_BARS,
  WAVE_MIN,
  emptyWave,
  filenameForMime,
  formatDuration,
  levelFromTimeDomain,
  pickMimeType,
  pushWaveLevel,
} from "./voiceWave.ts";

describe("formatDuration", () => {
  it("saniyeyi dakika:saniye olarak yazar", () => {
    assert.equal(formatDuration(0), "0:00");
    assert.equal(formatDuration(7), "0:07");
    assert.equal(formatDuration(59), "0:59");
    assert.equal(formatDuration(83), "1:23");
    assert.equal(formatDuration(600), "10:00");
  });

  it("kesirli ve negatif değer patlamaz", () => {
    assert.equal(formatDuration(7.9), "0:07");
    assert.equal(formatDuration(-3), "0:00");
  });
});

describe("levelFromTimeDomain", () => {
  /* AnalyserNode örneklerinde sessizlik 128 (merkez), ses merkezden sapma. */
  it("sessizlik sıfır seviye verir", () => {
    assert.equal(levelFromTimeDomain(new Array(64).fill(128)), 0);
  });

  it("boş tampon sıfırdır (analiz henüz başlamadıysa)", () => {
    assert.equal(levelFromTimeDomain([]), 0);
  });

  it("yüksek ses daha büyük seviye verir", () => {
    const kisik = levelFromTimeDomain([128, 138, 128, 118]);
    const yuksek = levelFromTimeDomain([128, 200, 128, 56]);
    assert.ok(kisik > 0);
    assert.ok(yuksek > kisik, "daha büyük sapma daha yüksek seviye");
  });

  it("seviye 1'i aşmaz (bağırınca dalga taşmasın)", () => {
    assert.equal(levelFromTimeDomain([255, 0, 255, 0]), 1);
  });
});

describe("pushWaveLevel", () => {
  it("dalga sola kayar: yeni seviye sağdan girer, en eski düşer", () => {
    const bars = pushWaveLevel([10, 20, 30], 1);
    assert.equal(bars.length, 3, "çubuk sayısı sabit kalır");
    assert.deepEqual(bars.slice(0, 2), [20, 30]);
    assert.equal(bars[2], 100, "tam seviye tam yükseklik");
  });

  it("sessizlikte bile en az bir çizgi kalır (kayıt sürüyor belli olsun)", () => {
    assert.equal(pushWaveLevel([50, 50], 0)[1], WAVE_MIN);
  });

  it("sınır dışı seviye kırpılır", () => {
    assert.equal(pushWaveLevel([50], 5)[0], 100);
    assert.equal(pushWaveLevel([50], -2)[0], WAVE_MIN);
  });

  it("boş dalga sabit sayıda ve en kısa çubuklarla başlar", () => {
    const bos = emptyWave();
    assert.equal(bos.length, WAVE_BARS);
    assert.ok(bos.every((h) => h === WAVE_MIN));
  });
});

describe("filenameForMime", () => {
  /* Groq biçimi dosya adının UZANTISINDAN anlar; uzantı MediaRecorder'ın
     gerçek mimeType'ından türer, varsayılmaz. */
  it("tarayıcının ürettiği biçimden uzantı çıkarır", () => {
    assert.equal(filenameForMime("audio/webm;codecs=opus"), "voice.webm");
    assert.equal(filenameForMime("audio/webm"), "voice.webm");
    assert.equal(filenameForMime("audio/ogg;codecs=opus"), "voice.ogg");
    assert.equal(filenameForMime("audio/mp4"), "voice.mp4", "iOS Safari mp4 üretir");
  });

  it("büyük harf ve boşluk sorun olmaz", () => {
    assert.equal(filenameForMime(" AUDIO/MP4 ; codecs=aac"), "voice.mp4");
  });

  it("bilinmeyen/boş biçim webm'e düşer (sunucu da allowlist uygular)", () => {
    assert.equal(filenameForMime("audio/tuhaf"), "voice.webm");
    assert.equal(filenameForMime(""), "voice.webm");
  });
});

describe("pickMimeType", () => {
  it("opus'lu webm'i tercih eder (küçük dosya, Groq'un en sevdiği)", () => {
    assert.equal(pickMimeType(() => true), "audio/webm;codecs=opus");
  });

  it("desteklenmeyeni atlar — Safari mp4'e düşer", () => {
    assert.equal(pickMimeType((m) => m === "audio/mp4"), "audio/mp4");
  });

  it("hiçbiri desteklenmiyorsa boş döner (tarayıcı kendi varsayılanını kullanır)", () => {
    assert.equal(pickMimeType(() => false), "");
  });
});

describe("kayıt sınırları", () => {
  it("üst sınır makul (cepte unutulan mikrofon kendiliğinden durur)", () => {
    assert.ok(MAX_RECORD_SECONDS > 30 && MAX_RECORD_SECONDS <= 300);
  });

  it("alt sınır kazara dokunuşu eler ama konuşmayı kesmez", () => {
    assert.ok(MIN_RECORD_SECONDS >= 1 && MIN_RECORD_SECONDS < 3);
  });
});
