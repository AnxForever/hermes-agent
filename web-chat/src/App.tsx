import { Navigate, Route, Routes } from "react-router-dom";

import ChatPage from "./pages/ChatPage";
import LoginPage from "./pages/LoginPage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/chat" element={<ChatPage />} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
