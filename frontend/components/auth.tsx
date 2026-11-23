"use client";
import { useState, type FormEvent } from "react";
import { api, type User } from "@/lib/api";

export function Auth({ onLogin, registrationOpen }: { onLogin: (user: User) => void; registrationOpen: boolean }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const response = await api<{ user: User }>(`/auth/${mode === "login" ? "login" : "register"}/`, Object.fromEntries(form));
      onLogin(response.user);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <main className="auth-page">
    <h1>Annotation verification</h1>
    <h2>{mode === "login" ? "Sign in" : "Create account"}</h2>
    <form onSubmit={submit} key={mode}>
      <label htmlFor="email">Email address</label>
      <input id="email" type="email" name="email" autoComplete="email" required disabled={busy} />
      <label htmlFor="password">Password</label>
      <input id="password" type="password" name="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required disabled={busy} />
      {error && <p className="error-message" role="alert">{error}</p>}
      <button type="submit" className="primary" disabled={busy}>{busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create your account"}</button>
    </form>
    {registrationOpen && <button className="link-button" disabled={busy} onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>{mode === "login" ? "Create account" : "Back to sign in"}</button>}
  </main>;
}
