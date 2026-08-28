import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import Layout from "./components/Layout";
import { getToken } from "./lib/auth";
import Admin from "./pages/Admin";
import Login from "./pages/Login";
import People from "./pages/People";
import PersonDetail from "./pages/PersonDetail";

/* Tek hesap: giriş yapan HER ŞEYE erişir (defter + admin), giriş yapmayan
 * HİÇBİR ŞEYE. Token yoksa doğrudan /login'e yönlendirilir; token
 * süresi dolmuşsa ilk API isteğinde 401 gelir ve lib/auth.onUnauthorized
 * aynı yere atar (bkz. api/client.ts, api/admin.ts). */
function ProtectedRoute({ children }: { children: ReactNode }) {
  const location = useLocation();
  if (!getToken()) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <div className="app">
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
          <Route path="/" element={<People />} />
          <Route path="/kisi/:id" element={<PersonDetail />} />
        </Route>
        {/* Yönetim paneli defterin Layout'unu KULLANMAZ: kendi sol menüsü
            var. Girişten sonra aynı token'la doğrudan çalışır, kendi
            şifre ekranı yok. */}
        <Route path="/admin" element={<ProtectedRoute><Admin /></ProtectedRoute>} />
      </Routes>
    </div>
  );
}
