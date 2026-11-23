"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type Annotation, type Code, type Detail, type SaveResponse } from "@/lib/api";

type Job = { action: string; payload: object; requestId: string };
const groups = ["RQ1", "RQ2", "RQ3"];

export function AnnotationEditor({ detail, codes, initialWorkbookSynced, onBusy, onSaved, onNext, hasNext }: {
  detail: Detail; codes: Code[]; initialWorkbookSynced: boolean;
  onBusy: (busy: boolean) => void; onSaved: (stage: Annotation["stage"]) => void; onNext: () => void; hasNext: boolean;
}) {
  const [annotation, setAnnotation] = useState(detail.annotation);
  const [aiCodes, setAiCodes] = useState(detail.record.ai_codes);
  const [pending, setPending] = useState(0);
  const [saveError, setSaveError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [workbookSynced, setWorkbookSynced] = useState(initialWorkbookSynced);
  const [syncBusy, setSyncBusy] = useState(false);
  const [transitioning, setTransitioning] = useState(false);
  const [search, setSearch] = useState("");
  const [showNotes, setShowNotes] = useState(Boolean(annotation.notes));
  const [syncError, setSyncError] = useState("");
  const jobs = useRef<Job[]>([]);
  const revision = useRef(annotation.revision);
  const processing = useRef(false);
  const stopped = useRef(false);
  const onSavedRef = useRef(onSaved);
  onSavedRef.current = onSaved;
  const locked = Boolean(annotation.independent_confirmed_at);
  const complete = annotation.stage === "complete";
  const selected = new Set(annotation.selected_codes);
  const aiSelected = useMemo(() => codes.filter(c => aiCodes?.includes(c.key)), [codes, aiCodes]);
  const reviewed = Object.keys(annotation.ai_reviews).length;

  const processQueue = useCallback(async () => {
    if (processing.current || stopped.current) return;
    processing.current = true;
    while (jobs.current.length > 0) {
      const job = jobs.current[0];
      try {
        const response = await api<SaveResponse>(`/records/${detail.record.id}/annotation/`, { action: job.action, ...job.payload, revision: revision.current, request_id: job.requestId });
        revision.current = response.annotation.revision;
        jobs.current.shift();
        setWorkbookSynced(response.workbook_synced);
        setAiCodes(response.ai_codes);
        const queueEmpty = jobs.current.length === 0;
        setAnnotation(current => queueEmpty ? response.annotation : {
          ...current, revision: response.annotation.revision, updated_at: response.annotation.updated_at,
        });
        onSavedRef.current(response.annotation.stage);
        setPending(jobs.current.length);
        setSaveError("");
        setConflict(false);
      } catch (e) {
        setSaveError((e as Error).message);
        setConflict(e instanceof ApiError && e.status === 409);
        stopped.current = true;
        break;
      }
    }
    setTransitioning(false);
    processing.current = false;
  }, [detail.record.id]);

  function enqueue(action: string, payload: object, optimistic?: Partial<Annotation>) {
    if (optimistic) setAnnotation(current => ({ ...current, ...optimistic }));
    jobs.current.push({ action, payload, requestId: crypto.randomUUID() });
    setPending(jobs.current.length);
    void processQueue();
  }

  useEffect(() => { onBusy(pending > 0 || Boolean(saveError)); }, [pending, saveError, onBusy]);
  useEffect(() => {
    function warn(event: BeforeUnloadEvent) {
      if (jobs.current.length) { event.preventDefault(); event.returnValue = ""; }
    }
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  function choose(code: string) {
    const next = selected.has(code) ? annotation.selected_codes.filter(c => c !== code) : [...annotation.selected_codes, code];
    enqueue("select_codes", { selected_codes: next, no_codes: false }, { selected_codes: next, no_codes: false });
  }
  function review(code: string, decision: "accepted" | "rejected") {
    const next = { ...annotation.ai_reviews, [code]: decision };
    enqueue("review_ai", { ai_reviews: next }, { ai_reviews: next, stage: "verify", completed_at: null });
  }
  async function retrySync() {
    setSyncBusy(true); setSyncError("");
    try { const response = await api<{ workbook_synced: boolean }>("/workbook/sync/", {}); setWorkbookSynced(response.workbook_synced); }
    catch (e) { setSyncError((e as Error).message); }
    finally { setSyncBusy(false); }
  }
  async function loadLatest() {
    if (!window.confirm("Load the saved version? Unsaved edits in this tab will be discarded. Annotations already saved to your account will be kept.")) return;
    try {
      const latest = await api<Detail>(`/records/${detail.record.id}/`);
      jobs.current = [];
      stopped.current = false;
      revision.current = latest.annotation.revision;
      setAnnotation(latest.annotation); setAiCodes(latest.record.ai_codes);
      setPending(0); setSaveError(""); setConflict(false);
      onSavedRef.current(latest.annotation.stage);
    } catch (e) { setSaveError((e as Error).message); }
  }
  const filtered = codes.filter(code => `${code.header} ${code.key}`.toLowerCase().includes(search.toLowerCase()));
  const disabled = Boolean(saveError) || transitioning;

  return <>
    <div className="save-status" role="status" aria-live="polite">
      {saveError ? "Save paused" : pending ? `Saving changes${pending > 1 ? ` (${pending})` : ""}…` : workbookSynced ? "All changes saved" : "Account saved · XLSX pending"}
    </div>
    {saveError && <div className="error-message" role="alert">
      <p>{saveError} {conflict ? "Load the saved version to discard unsaved edits in this tab." : "Keep this page open until your changes are saved."}</p>
      {conflict ? <button onClick={loadLatest}>Load saved version</button> : <button onClick={() => { stopped.current = false; setSaveError(""); void processQueue(); }}>Retry saving</button>}
    </div>}
    {!workbookSynced && !pending && <div className="error-message" role="alert">
      <p>Saved to your account. The server workbook needs a sync. {syncError}</p>
      <button disabled={syncBusy} onClick={retrySync}>{syncBusy ? "Syncing…" : "Sync XLSX"}</button>
    </div>}
    <div className="annotation-columns">
      <article className="post-content">
        <p className="record-meta">r/{detail.record.subreddit} · {detail.record.external_id} · Row {detail.record.source_row}</p>
        <h2>{detail.record.title}</h2>
        <div className="post-text">{detail.record.text || "This post has no body text."}</div>
        <div className="notes">
          <button className="link-button" onClick={() => setShowNotes(!showNotes)} aria-expanded={showNotes}>Research notes (optional)</button>
          {showNotes && <>
            <label className="sr-only" htmlFor="rater-notes">Research notes</label>
            <textarea id="rater-notes" value={annotation.notes} maxLength={10000} disabled={disabled} onChange={e => enqueue("save_notes", { notes: e.target.value }, { notes: e.target.value })} />
          </>}
        </div>
      </article>
      <section className="annotation-form" aria-label={locked ? "AI verification" : "Independent coding"}>
        {!locked ? <>
          <h2>1. Select your codes</h2>
          <p className="instructions">Select all applicable codes. Confirming locks your choices and reveals the AI annotations.</p>
          <input type="search" aria-label="Search codes" placeholder="Search codes" value={search} onChange={e => setSearch(e.target.value)} />
          <div className="code-list">
            {groups.map(group => {
              const groupCodes = filtered.filter(code => code.group === group);
              return groupCodes.length > 0 && <fieldset key={group}>
                <legend>{group}</legend>
                {groupCodes.map(code => <label key={code.key} className="code-choice">
                  <input type="checkbox" aria-label={code.header} checked={selected.has(code.key)} disabled={disabled} onChange={() => choose(code.key)} />
                  <span>{code.header}</span>
                </label>)}
              </fieldset>;
            })}
            {filtered.length === 0 && <p className="help-text">No codes match your search.</p>}
          </div>
          <div className="form-actions">
            <label className="checkbox-label"><input type="checkbox" checked={annotation.no_codes} disabled={disabled} onChange={e => enqueue("select_codes", { selected_codes: [], no_codes: e.target.checked }, { selected_codes: [], no_codes: e.target.checked })} />No applicable codes for this post</label>
            <p>{annotation.selected_codes.length} codes selected</p>
            <button className="primary" disabled={disabled || pending > 0 || (!annotation.selected_codes.length && !annotation.no_codes)} onClick={() => { setTransitioning(true); enqueue("confirm_independent", {}); }}>Confirm codes & reveal AI</button>
          </div>
        </> : <>
          <h2>{complete ? "Verification complete" : "2. Verify AI annotations"}</h2>
          <p className="instructions">Accept or reject each code marked 1 by the AI. Your independent codes are locked.</p>
          <details className="independent-codes">
            <summary>Your independent codes ({annotation.selected_codes.length})</summary>
            {annotation.no_codes ? <p>No applicable codes.</p> : <ul>{codes.filter(code => selected.has(code.key)).map(code => <li key={code.key}>{code.header}</li>)}</ul>}
          </details>
          <p className="review-count">{reviewed} / {aiSelected.length} AI codes reviewed</p>
          <div className="ai-code-list">
            {aiSelected.map(code => <div key={code.key} className="ai-review-row">
              <p>{code.header}</p>
              <p className="help-text">Your independent selection: {selected.has(code.key) ? "Yes" : "No"}</p>
              <div className="decision-buttons">
                <button disabled={disabled} aria-label={`Accept ${code.label}`} aria-pressed={annotation.ai_reviews[code.key] === "accepted"} onClick={() => review(code.key, "accepted")}>Accept</button>
                <button disabled={disabled} aria-label={`Reject ${code.label}`} aria-pressed={annotation.ai_reviews[code.key] === "rejected"} onClick={() => review(code.key, "rejected")}>Reject</button>
              </div>
            </div>)}
            {aiSelected.length === 0 && <p>No AI-positive codes for this post.</p>}
          </div>
          <div className="form-actions">
            {complete ? hasNext ? <button className="primary" disabled={pending > 0 || disabled} onClick={onNext}>Next unfinished post</button> : <p>All posts are complete.</p> : <button className="primary" disabled={disabled || pending > 0 || reviewed !== aiSelected.length} onClick={() => { setTransitioning(true); enqueue("complete", {}); }}>Complete verification</button>}
          </div>
        </>}
      </section>
    </div>
  </>;
}
