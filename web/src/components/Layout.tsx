import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { ActionBarProvider } from "../lib/actionBar";
import { clearToken } from "../lib/auth";
import { hhmm, money } from "../lib/format";
import type { DrawerEvent } from "../lib/sideDrawer";
import { nextDrawerState } from "../lib/sideDrawer";
import { applyTheme, getStoredTheme, type Theme } from "../lib/theme";
import { useToast } from "../lib/toast";
import ChatWidget from "./ChatWidget";
import SettingsModal from "./SettingsModal";

/* Hamburger — ikonlar elle çizilir (CLAUDE.md > arayüz kuralları: harici
 * bileşen kütüphanesi yok). currentColor kullanır, iki temada da doğru
 * renklenir; çizgiler kalın ve aralıklı, uzaktan/parmakla net seçilir. */
function MenuIcon() {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" focusable="false">
      <path d="M4 7h16M4 12h16M4 17h16" fill="none" stroke="currentColor" strokeWidth="2.2"
        strokeLinecap="round" />
    </svg>
  );
}

export default function Layout() {
  const nav = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();
  const toast = useToast();
  const [showSettings, setShowSettings] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(() => new Date());
  const [refreshing, setRefreshing] = useState(false);
  const [theme, setTheme] = useState<Theme>(getStoredTheme);
  /* Dar ekranda (<720px) sol panel çekmeceye döner: varsayılan gizli, ☰ ile
   * açılır. Masaüstünde bu state hiçbir şeye karışmaz — oradaki panel
   * CSS'te sabit yan panel olarak kalır (bkz. index.css > mobil çekmece). */
  const [menuOpen, setMenuOpen] = useState(false);
  /* Alt eylem barının iki yuvası. Sayfalar (People/PersonDetail) ve
   * ChatWidget butonlarını buraya portal'lar — bkz. lib/actionBar.tsx. */
  const [barMain, setBarMain] = useState<HTMLElement | null>(null);
  const [barSide, setBarSide] = useState<HTMLElement | null>(null);
  const barSlots = useMemo(() => ({ main: barMain, side: barSide }), [barMain, barSide]);

  /* Menüyü açan/kapatan tek kapı: kural sideDrawer.ts'te, orada test edilir. */
  function drawer(event: DrawerEvent) {
    setMenuOpen((open) => nextDrawerState(open, event));
  }

  function chooseTheme(next: Theme) {
    setTheme(next);
    applyTheme(next);
  }

  function logout() {
    clearToken();
    nav("/login", { replace: true });
  }

  const people = useQuery({ queryKey: ["persons"], queryFn: () => api.persons() });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.getSettings });

  /* Menüden bir yere gidilince çekmece arkada açık kalmasın. */
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") drawer("escape");
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const rows = people.data ?? [];
  const businessName = settings.data?.business_name ?? "Hesaplık";
  const onDefter = location.pathname === "/";

  const toplamAlacak = rows
    .filter((p) => Number(p.balance_try) > 0)
    .reduce((acc, p) => acc + Number(p.balance_try), 0);
  const toplamBorc = rows
    .filter((p) => Number(p.balance_try) < 0)
    .reduce((acc, p) => acc + Math.abs(Number(p.balance_try)), 0);

  async function refresh() {
    setRefreshing(true);
    await qc.invalidateQueries();
    setUpdatedAt(new Date());
    toast("Sistem güncellendi", "success");
    setRefreshing(false);
  }

  return (
    <div className="layout">
      {/* Yalnızca dar ekranda görünür (CSS). Sabit değil, akışta duran ama
          yapışkan bir şerit: içeriğin üstüne binmez, kaydırınca kaybolmaz. */}
      <header className="mobile-bar">
        <button
          className="menu-toggle"
          onClick={() => drawer("toggle")}
          aria-label={menuOpen ? "Menüyü kapat" : "Menüyü aç"}
          aria-expanded={menuOpen}
          aria-controls="yan-panel"
        >
          <MenuIcon />
        </button>
        <span className="mobile-bar-title">{businessName}</span>
      </header>

      {menuOpen && (
        <div className="side-backdrop" onClick={() => drawer("backdrop")} aria-hidden="true" />
      )}

      <aside id="yan-panel" className={`side-panel${menuOpen ? " side-panel-open" : ""}`}>
        <div className="side-head">
          <div className="side-head-text">
            <h1 className="side-title">{businessName}</h1>
            <p className="side-subtitle">Hesap defteri</p>
          </div>
          <button className="side-close" onClick={() => drawer("close")} aria-label="Menüyü kapat">
            ✕
          </button>
        </div>

        <button
          className={`side-back ${!onDefter ? "side-back-active" : ""}`}
          onClick={() => {
            nav("/");
            drawer("navigate");
          }}
        >
          ← Defter
        </button>

        <div className="metrics">
          <div className="metric-card">
            <div className="metric-label">Kişi sayısı</div>
            <div className="metric-value">{rows.length}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Toplam alacak</div>
            <div className="metric-value borc">{money(toplamAlacak)}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Toplam borç</div>
            <div className="metric-value tahsilat">{money(toplamBorc)}</div>
          </div>
        </div>

        <div className="side-reports">
          <button
            className="side-back"
            onClick={() => api.openDailyReport().catch(() => toast("Rapor alınamadı", "info"))}
          >
            <img className="report-icon" src="/icons/pdf_logo.svg" alt="" /> Günlük rapor
          </button>
          <button
            className="side-back"
            onClick={() => api.openGeneralReport().catch(() => toast("Rapor alınamadı", "info"))}
          >
            <img className="report-icon" src="/icons/pdf_logo.svg" alt="" /> Genel rapor
          </button>
        </div>

        <div className="side-actions">
          <button
            className="side-icon-btn"
            onClick={refresh}
            disabled={refreshing}
            aria-label="Yenile"
            title="Yenile"
          >
            ⟳
          </button>
          <button
            className="side-icon-btn"
            onClick={() => {
              setShowSettings(true);
              drawer("close");
            }}
            aria-label="Ayarlar"
            title="Ayarlar"
          >
            ⚙
          </button>
          <button
            className="side-icon-btn"
            onClick={() => chooseTheme(theme === "dark" ? "light" : "dark")}
            aria-label={theme === "dark" ? "Açık moda geç" : "Koyu moda geç"}
            title={theme === "dark" ? "Açık moda geç" : "Koyu moda geç"}
          >
            {theme === "dark" ? "☀" : "☾"}
          </button>
          <span className="side-updated">Güncellendi · {hhmm(updatedAt)}</span>
        </div>

        <button className="side-back" onClick={logout}>
          Çıkış yap
        </button>
      </aside>

      <ActionBarProvider value={barSlots}>
        <div className="side-main">
          <Outlet />
        </div>

        {/* Alt eylem barı: OPAK ve sabit. Sayfa kaydırılınca içerik barın
            ARKASINDAN geçmez — .side-main'in alt boşluğu (--bar-space) tam
            bar yüksekliği kadardır, son satır barın hemen üstünde biter. */}
        <div className="action-bar">
          <div className="action-bar-inner">
            <div className="action-bar-main" ref={setBarMain} />
            <div className="action-bar-side" ref={setBarSide} />
          </div>
        </div>

        {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
        <ChatWidget />
      </ActionBarProvider>
    </div>
  );
}
