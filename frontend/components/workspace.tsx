"use client";
import { useEffect, useState } from "react";
import { api, type Project, type User, type Detail, type RecordSummary } from "@/lib/api";
import { AnnotationEditor } from "./editor";

const statusLabels = { not_started: "Not started", annotate: "In progress", verify: "AI review", complete: "Complete" };

export function Workspace({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [project, setProject] = useState<Project | null>(null);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api<Project>("/project/", undefined, controller.signal).then(result => {
      setProject(result);
      setActiveId(result.records.find(r => r.stage !== "complete")?.id ?? result.records[0]?.id ?? null);
    }).catch(e => { if (e.name !== "AbortError") setError(e.message); });
    return () => controller.abort();
  }, [reload]);

  useEffect(() => {
    if (activeId === null) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setDetail(null);
    api<Detail>(`/records/${activeId}/`, undefined, controller.signal).then(setDetail)
      .catch(e => { if (e.name !== "AbortError") setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [activeId, reload]);

  const records = project?.records || [];
  const completed = records.filter(r => r.stage === "complete").length;
  const activeIndex = records.findIndex(r => r.id === activeId);

  function openRecord(id: number) {
    if (!busy) setActiveId(id);
  }

  async function logout() {
    setLogoutBusy(true);
    try { await api("/auth/logout/", {}); onLogout(); }
    catch (e) { setError((e as Error).message); }
    finally { setLogoutBusy(false); }
  }

  async function resetAnnotations() {
    const typed = window.prompt("This permanently deletes ALL of your annotations, AI decisions and notes on every post. Other raters are not affected. This cannot be undone.\n\nType RESET to confirm.");
    if (typed === null) return;
    if (typed.trim() !== "RESET") { window.alert("Nothing was deleted. Type RESET exactly to confirm."); return; }
    setBusy(true);
    try {
      await api("/annotations/reset/", { confirm: "RESET" });
      setActiveId(null);
      setReload(r => r + 1);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  function updateStage(id: number, stage: RecordSummary["stage"]) {
    setProject(p => p ? { ...p, records: p.records.map(r => r.id === id ? { ...r, stage } : r) } : p);
  }

  return <main className="workspace">
    <header className="app-header">
      <h1>Annotation verification</h1>
      <div className="account-controls">
        <span>{user.email}</span>
        <a href="/api/export.xlsx" aria-disabled={busy} onClick={e => { if (busy) e.preventDefault(); }}>Download my annotations</a>
        <button className="danger" disabled={busy || logoutBusy} onClick={resetAnnotations}>Reset my annotations</button>
        <button disabled={busy || logoutBusy} onClick={logout}>{logoutBusy ? "Signing out…" : "Sign out"}</button>
      </div>
    </header>
    <nav className="post-controls" aria-label="Post navigation">
      <label htmlFor="post-select">Post</label>
      <select id="post-select" value={activeId ?? ""} disabled={busy || !records.length} onChange={e => openRecord(Number(e.target.value))}>
        {!records.length && <option value="">Loading…</option>}
        {records.map((record, index) => <option key={record.id} value={record.id}>{index + 1}. {record.title} — {statusLabels[record.stage]}</option>)}
      </select>
      <button disabled={busy || activeIndex <= 0} onClick={() => openRecord(records[activeIndex - 1].id)}>Previous post</button>
      <button disabled={busy || activeIndex < 0 || activeIndex >= records.length - 1} onClick={() => openRecord(records[activeIndex + 1].id)}>Next post</button>
      <span className="progress-text">{completed} / {records.length} complete</span>
    </nav>
    {error ? <div className="error-message" role="alert">
      <p>{error}</p>
      <button onClick={() => { setError(""); setReload(r => r + 1); }}>Retry connection</button>
    </div> : loading || !project || !detail ? <p className="loading-message">Loading post…</p> : <AnnotationEditor
      key={`${detail.record.id}-${reload}`}
      detail={detail}
      codes={project.codes}
      initialWorkbookSynced={project.dataset.workbook_synced}
      onBusy={setBusy}
      onSaved={stage => updateStage(detail.record.id, stage)}
      onNext={() => {
        const next = records.slice(activeIndex + 1).find(r => r.stage !== "complete") || records.find(r => r.id !== activeId && r.stage !== "complete");
        if (next) openRecord(next.id);
      }}
      hasNext={records.some(r => r.id !== activeId && r.stage !== "complete")}
    />}
  </main>;
}
