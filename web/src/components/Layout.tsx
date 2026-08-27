import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { hhmm, money } from "../lib/format";
import { applyTheme, getStoredTheme, type Theme } from "../lib/theme";
import { useToast } from "../lib/toast";
import SettingsModal from "./SettingsModal";

export default function Layout() {
  const nav = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();
  const toast = useToast();
  const [showSettings, setShowSettings] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(() => new Date());
  const [refreshing, setRefreshing] = useState(false);
  const [theme, setTheme] = useState<Theme>(getStoredTheme);

  function chooseTheme(next: Theme) {
    setTheme(next);
    applyTheme(next);
  }

  const people = useQuery({ queryKey: ["persons"], queryFn: () => api.persons() });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.getSettings });

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
      <aside className="side-panel">
        <h1 className="side-title">{businessName}</h1>
        <p className="side-subtitle">Hesap defteri</p>

        <button
          className={`side-back ${!onDefter ? "side-back-active" : ""}`}
          onClick={() => nav("/")}
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
          <button className="side-back" onClick={() => window.open(api.dailyReportUrl(), "_blank")}>
            <img className="report-icon" src="/icons/pdf_logo.svg" alt="" /> Günlük rapor
          </button>
          <button className="side-back" onClick={() => window.open(api.generalReportUrl(), "_blank")}>
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
            onClick={() => setShowSettings(true)}
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
      </aside>

      <div className="side-main">
        <Outlet />
      </div>

      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
    </div>
  );
}
