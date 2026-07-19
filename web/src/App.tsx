import { Route, Routes } from "react-router-dom";

import People from "./pages/People";
import PersonDetail from "./pages/PersonDetail";

export default function App() {
  return (
    <div className="app">
      <Routes>
        <Route path="/" element={<People />} />
        <Route path="/kisi/:id" element={<PersonDetail />} />
      </Routes>
    </div>
  );
}
