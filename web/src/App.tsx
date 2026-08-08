import { Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import Admin from "./pages/Admin";
import People from "./pages/People";
import PersonDetail from "./pages/PersonDetail";

export default function App() {
  return (
    <div className="app">
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<People />} />
          <Route path="/kisi/:id" element={<PersonDetail />} />
        </Route>
        <Route path="/admin" element={<Admin />} />
      </Routes>
    </div>
  );
}
