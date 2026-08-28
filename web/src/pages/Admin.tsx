/* Admin paneli (/admin) — Faz 7.
 *
 * Sol menü + sağ içerik. Yalnızca "Kontroller" (LLM aç/kapat, model, yedek
 * — yazma işlemleri) henüz "Yakında"; diğer tüm bölümler dolu. Yeni dört
 * bölüm (LLM İzleme, İstek Kuyruğu, Loglar, Kişiler & İşlemler) salt
 * okunur — bkz. admin.md.
 *
 * Oturum: tek hesabın ortak JWT'si (bkz. App.tsx > ProtectedRoute). Bu
 * sayfaya token'sız hiç girilmez; token süresi mid-session dolarsa ilk
 * admin API isteği 401 döner ve onUnauthorized() (lib/auth) girişe atar —
 * panelin kendi ayrı şifre ekranı YOK, LLM Yönetimi de dahil aynı token'la
 * çalışır.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "../api/client";
import type { AdminLLMStatus, AdminVllmControl } from "../api/types";
import AdminBackups from "../components/admin/AdminBackups";
import AdminData from "../components/admin/AdminData";
import AdminFlow from "../components/admin/AdminFlow";
import AdminHealth from "../components/admin/AdminHealth";
import AdminLlmMonitor from "../components/admin/AdminLlmMonitor";
import AdminLogs from "../components/admin/AdminLogs";
import AdminQueue from "../components/admin/AdminQueue";
import { onUnauthorized } from "../lib/auth";
import { useToast } from "../lib/toast";

type SectionId =
  | "flow"
  | "health"
  | "backups"
  | "llm"
  | "llm-monitor"
  | "queue"
  | "logs"
  | "data"
  | "controls";

type Section = { id: SectionId; label: string; hint: string; ready?: boolean };

const SECTIONS: Section[] = [
  { id: "flow", label: "İşlem Akışı", hint: "Ne yazıldı → ne algılandı → ne yapıldı", ready: true },
  {
    id: "health",
    label: "Sistem Sağlığı",
    hint: "API, bot, veritabanı, Ollama, yedek durumu",
    ready: true,
  },
  {
    id: "backups",
    label: "Yedekleme",
    hint: "Tüm yedekler ve geri yükleme",
    ready: true,
  },
  {
    id: "llm",
    label: "LLM Yönetimi",
    hint: "Kaynak durumu, vLLM/Ollama tercihi",
    ready: true,
  },
  {
    id: "llm-monitor",
    label: "LLM İzleme",
    hint: "LLM'e düşen mesajlar, süre, başarı oranı",
    ready: true,
  },
  {
    id: "queue",
    label: "İstek Kuyruğu",
    hint: "Bekleyen ve yarım kalan istekler",
    ready: true,
  },
  {
    id: "logs",
    label: "Loglar",
    hint: "Sistem denetim kayıtları (audit_log)",
    ready: true,
  },
  {
    id: "data",
    label: "Kişiler & İşlemler",
    hint: "Salt okunur veri gezgini, arşiv",
    ready: true,
  },
  { id: "controls", label: "Kontroller", hint: "LLM aç/kapat, model, yedek" },
];

export default function Admin() {
  const [section, setSection] = useState<SectionId>("flow");

  const active = SECTIONS.find((s) => s.id === section)!;

  return (
    <div className="admin">
      <aside className="admin-side">
        <div className="admin-brand">
          <span className="admin-brand-name">Hesaplık</span>
          <span className="admin-brand-tag">yönetim</span>
        </div>

        <nav className="admin-nav">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              className={`admin-nav-item ${s.id === section ? "admin-nav-active" : ""}`}
              onClick={() => setSection(s.id)}
            >
              <span>{s.label}</span>
              {!s.ready && <span className="admin-soon">yakında</span>}
            </button>
          ))}
        </nav>

        <div className="admin-side-foot">
          <a className="admin-side-link" href="/">
            ← Deftere dön
          </a>
          <button className="admin-side-link" onClick={onUnauthorized}>
            Çıkış yap
          </button>
        </div>
      </aside>

      <main className="admin-main">
        <header className="admin-head">
          <h1>{active.label}</h1>
          <p>{active.hint}</p>
        </header>

        {section === "flow" && <AdminFlow onUnauthorized={onUnauthorized} />}
        {section === "health" && <AdminHealth onUnauthorized={onUnauthorized} />}
        {section === "backups" && <AdminBackups onUnauthorized={onUnauthorized} />}
        {section === "llm" && <LlmPanel />}
        {section === "llm-monitor" && <AdminLlmMonitor onUnauthorized={onUnauthorized} />}
        {section === "queue" && <AdminQueue onUnauthorized={onUnauthorized} />}
        {section === "logs" && <AdminLogs onUnauthorized={onUnauthorized} />}
        {section === "data" && <AdminData onUnauthorized={onUnauthorized} />}
        {!active.ready && <Soon />}
      </main>
    </div>
  );
}

function Soon() {
  return (
    <div className="admin-soon-box">
      <h2>Yakında</h2>
      <p>Bu bölüm henüz doldurulmadı. İlk gerçek içerik "İşlem Akışı" bölümünde.</p>
    </div>
  );
}

const PREFERENCES: { value: string; label: string }[] = [
  { value: "auto", label: "Otomatik" },
  { value: "nvidia", label: "NVIDIA zorla" },
  { value: "vllm", label: "vLLM zorla" },
  { value: "ollama", label: "Ollama zorla" },
  { value: "none", label: "Kapalı" },
];

const SOURCE_LABEL: Record<AdminLLMStatus["active"], string> = {
  nvidia: "NVIDIA",
  vllm: "vLLM",
  ollama: "Ollama",
  none: "Kapalı",
};

function StatusDot({ ok }: { ok: boolean }) {
  return <span className={`status-dot ${ok ? "status-dot-ok" : "status-dot-down"}`} />;
}

function LlmPanel() {
  const qc = useQueryClient();
  const toast = useToast();

  const status = useQuery({
    queryKey: ["admin-llm"],
    queryFn: () => api.adminLlmStatus(),
    refetchInterval: 10_000,
  });

  const setPrimary = useMutation({
    mutationFn: (llmPrimary: string) => api.adminSetLlmPrimary(llmPrimary),
    onSuccess: (data) => {
      qc.setQueryData(["admin-llm"], data);
      toast("LLM tercihi güncellendi", "success");
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : "Güncelleme başarısız", "info"),
  });

  if (status.isLoading) return <p className="muted">Yükleniyor…</p>;
  if (status.error) {
    return (
      <div className="error">
        {status.error instanceof ApiError ? status.error.message : "Durum alınamadı"}
      </div>
    );
  }

  const data = status.data!;

  return (
    <>
      <div className="panel">
        <p className="panel-title">Şu an aktif</p>
        <div className="admin-active">
          <StatusDot ok={data.active !== "none"} />
          <strong>{SOURCE_LABEL[data.active]}</strong>
        </div>
      </div>

      <div className="panel">
        <p className="panel-title">Kaynak durumu</p>
        <div className="admin-source">
          <span className="admin-source-name">
            <StatusDot ok={data.nvidia.ok} />
            NVIDIA (bulut)
          </span>
          <span className="admin-source-meta">
            {data.nvidia.ok ? data.nvidia.model : "erişilemiyor"}
          </span>
        </div>
        <div className="admin-source">
          <span className="admin-source-name">
            <StatusDot ok={data.vllm.ok} />
            vLLM (Bosna)
          </span>
          <span className="admin-source-meta">{data.vllm.ok ? data.vllm.model : "erişilemiyor"}</span>
        </div>
        <div className="admin-source">
          <span className="admin-source-name">
            <StatusDot ok={data.ollama.ok} />
            Ollama (İzmir)
          </span>
          <span className="admin-source-meta">
            {data.ollama.ok ? data.ollama.model : "erişilemiyor"}
          </span>
        </div>
      </div>

      <div className="panel">
        <p className="panel-title">Tercih</p>
        <div className="admin-pref">
          {PREFERENCES.map((p) => (
            <button
              key={p.value}
              aria-pressed={data.primary === p.value}
              disabled={setPrimary.isPending}
              onClick={() => setPrimary.mutate(p.value)}
            >
              {p.label}
            </button>
          ))}
        </div>
        {data.primary === "ollama" && (
          <p className="hint" style={{ marginTop: 10 }}>
            NVIDIA'ya ve vLLM'e hiç dokunulmuyor — GPU'yu kendiniz kullanabilirsiniz.
          </p>
        )}
      </div>

      <VllmControlPanel />
    </>
  );
}

/* vLLM cihazı aç/kapat ("Yol B"): panel Bosna'ya doğrudan komut göndermez,
 * yalnızca bir TERCİH yazar (/api/admin/vllm-control). Bosna'daki host
 * script'i bu tercihi kendi çekip uygular (~30 sn gecikme payı) — bu yüzden
 * "istenen" (panelin yazdığı) ile "gerçek" (vLLM şu an cevap veriyor mu) ayrı
 * gösterilir; ikisi arasında fark varsa "başlatılıyor/kapatılıyor" denir. */
function gercekDurumMetni(data: AdminVllmControl): string {
  if (data.pending) return data.desired === "on" ? "Başlatılıyor… (~30 sn)" : "Kapatılıyor… (~30 sn)";
  return data.desired === "on" ? "Erişilebilir" : "Kapalı";
}

function VllmControlPanel() {
  const qc = useQueryClient();
  const toast = useToast();

  const control = useQuery({
    queryKey: ["admin-vllm-control"],
    queryFn: () => api.adminVllmControl(),
    refetchInterval: 5_000,
  });

  const setDesired = useMutation({
    mutationFn: (desired: "on" | "off") => api.adminSetVllmDesired(desired),
    onSuccess: (data) => {
      qc.setQueryData(["admin-vllm-control"], data);
      toast(
        data.desired === "on"
          ? "vLLM açılıyor — ~30 saniyede uygulanır"
          : "vLLM kapatılıyor — ~30 saniyede uygulanır",
        "success",
      );
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : "Güncelleme başarısız", "info"),
  });

  if (control.isLoading || control.error) return null;

  const data = control.data!;

  return (
    <div className="panel">
      <p className="panel-title">vLLM Cihazı (Bosna 2080 Super)</p>

      <div className="admin-source">
        <span className="admin-source-name">İstenen</span>
        <span className="admin-source-meta">{data.desired === "on" ? "Açık" : "Kapalı"}</span>
      </div>
      <div className="admin-source">
        <span className="admin-source-name">
          <StatusDot ok={data.reachable} />
          Gerçek
        </span>
        <span className="admin-source-meta">{gercekDurumMetni(data)}</span>
      </div>

      <div className="admin-pref" style={{ marginTop: 10 }}>
        <button
          aria-pressed={data.desired === "on"}
          disabled={setDesired.isPending || data.desired === "on"}
          onClick={() => setDesired.mutate("on")}
        >
          vLLM'i Aç
        </button>
        <button
          aria-pressed={data.desired === "off"}
          disabled={setDesired.isPending || data.desired === "off"}
          onClick={() => setDesired.mutate("off")}
        >
          vLLM'i Kapat
        </button>
      </div>

      <p className="hint" style={{ marginTop: 10 }}>
        Kapatınca ekran kartının belleği boşalır, GPU'yu başka iş için
        kullanabilirsiniz. Değişiklik ~30 saniyede uygulanır.
      </p>
    </div>
  );
}
