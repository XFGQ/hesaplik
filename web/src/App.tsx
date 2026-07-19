import { Route, Routes } from "react-router-dom";

import AddEntry from "./pages/AddEntry";
import People from "./pages/People";
import PersonDetail from "./pages/PersonDetail";
import PersonForm from "./pages/PersonForm";

export default function App() {
  return (
    <div className="app">
      <Routes>
        <Route path="/" element={<People />} />
        <Route path="/kisi/yeni" element={<PersonForm />} />
        <Route path="/kisi/:id" element={<PersonDetail />} />
        <Route path="/kisi/:id/duzenle" element={<PersonForm />} />
        <Route path="/kisi/:id/ekle" element={<AddEntry />} />
      </Routes>
    </div>
  );
}
