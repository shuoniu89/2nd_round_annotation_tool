"use client";
import { useEffect, useState } from "react";
import { api, type User } from "@/lib/api";
import { Auth } from "@/components/auth";
import { Workspace } from "@/components/workspace";

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [registrationOpen, setRegistrationOpen] = useState(true);
  async function loadSession() {
    setError(""); setLoading(true);
    try {
      const result = await api<{ user: User | null; registration_open: boolean }>("/session/");
      setUser(result.user); setRegistrationOpen(result.registration_open);
    } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  }
  useEffect(() => { void loadSession(); }, []);
  if (loading || error) return <main className="auth-page">
    <h1>Annotation verification</h1>
    {loading ? <p>Loading…</p> : <><p className="error-message" role="alert">{error}</p><button onClick={loadSession}>Retry connection</button></>}
  </main>;
  return user ? <Workspace user={user} onLogout={() => { setUser(null); void loadSession(); }} /> : <Auth onLogin={setUser} registrationOpen={registrationOpen} />;
}
