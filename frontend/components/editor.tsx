"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type Annotation, type Code, type Detail, type SaveResponse } from "@/lib/api";
import { buildTaxonomy } from "@/lib/taxonomy";
import { CodeTree } from "@/components/code-tree";

type Job = { action: string; payload: object; requestId: string };

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
  // Step 1 is editable while stage is "annotate", including after "Edit my codes" in step 2.
  const locked = annotation.stage !== "annotate";
  const revealedBefore = Boolean(annotation.independent_confirmed_at);
  const complete = annotation.stage === "complete";
  const selected = new Set(annotation.selected_codes);
  const taxonomy = useMemo(() => buildTaxonomy(codes), [codes]);
  // Theme → subtheme order, matching the codebook table, for every list on this screen.
  const ordered = useMemo(() => [...codes].sort((a, b) =>
    (taxonomy.order.get(a.key) ?? 1e6 + a.column) - (taxonomy.order.get(b.key) ?? 1e6 + b.column)), [codes, taxonomy]);
  // Only AI subthemes are verified; AI theme codes are not shown.
  const aiSelected = useMemo(() => ordered.filter(c => c.kind === "detailed" && aiCodes?.includes(c.key)), [ordered, aiCodes]);
  const themeCount = annotation.selected_codes.filter(key => codes.find(c => c.key === key)?.kind === "broad").length;
  // Selected themes that offer subthemes but have none selected yet.
  const missingSubthemes = ordered.filter(code => {
    const children = taxonomy.childrenOf.get(code.key) ?? [];
    return selected.has(code.key) && children.length > 0 && !children.some(key => selected.has(key));
  });
  function confirmIndependent() {
    if (missingSubthemes.length && !window.confirm(
      `These themes have no subtheme selected:\n\n${missingSubthemes.map(c => `• ${c.label}`).join("\n")}\n\n` +
      "Select OK to confirm anyway, or Cancel to go back and choose subthemes.")) return;
    setTransitioning(true);
    enqueue("confirm_independent", {});
  }
  function editCodes() {
    if (!window.confirm("Go back and edit your codes? Your AI decisions are kept, and you will return to verification after confirming again. Your original choices from before seeing the AI are kept on record.")) return;
    setTransitioning(true);
    enqueue("edit_codes", {}, { stage: "annotate", completed_at: null });
  }
  // AI codes the rater also chose in step 1 are agreed automatically; only the rest need a decision.
  const agreedCodes = aiSelected.filter(c => selected.has(c.key));
  const toReview = aiSelected.filter(c => !selected.has(c.key));
  const reviewed = toReview.filter(c => ["accepted", "rejected"].includes(annotation.ai_reviews[c.key])).length;

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

  function setSelection(next: string[]) {
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
          {revealedBefore
            ? <p className="warning-message">You are editing your codes after seeing the AI annotations. Your original choices from before the reveal are kept on record.</p>
            : <p className="instructions">Select every applicable theme, then the subthemes that apply under it. Selecting a subtheme also selects its theme. Confirming reveals the AI annotations.</p>}
          <div className="code-tools">
            <input type="search" aria-label="Search codes" placeholder="Search themes and subthemes" value={search} onChange={e => setSearch(e.target.value)} />
            <button type="button" disabled={disabled || annotation.selected_codes.length === 0}
              onClick={() => { if (window.confirm("Clear all selected themes and subthemes for this post?")) setSelection([]); }}>Clear all</button>
          </div>
          <div className="code-list">
            <CodeTree taxonomy={taxonomy} selected={selected} disabled={disabled} search={search} onChange={setSelection} />
          </div>
          <div className="form-actions">
            <label className="checkbox-label"><input type="checkbox" checked={annotation.no_codes} disabled={disabled} onChange={e => enqueue("select_codes", { selected_codes: [], no_codes: e.target.checked }, { selected_codes: [], no_codes: e.target.checked })} />No applicable codes for this post</label>
            <p>{themeCount} {themeCount === 1 ? "theme" : "themes"} · {annotation.selected_codes.length - themeCount} {annotation.selected_codes.length - themeCount === 1 ? "subtheme" : "subthemes"} selected</p>
            {missingSubthemes.length > 0 && <div className="warning-message" role="alert">
              <strong>Choose a subtheme for {missingSubthemes.length === 1 ? "this theme" : "these themes"}:</strong>
              <ul>{missingSubthemes.map(c => <li key={c.key}>{c.label}</li>)}</ul>
            </div>}
            <button className="primary" disabled={disabled || pending > 0 || (!annotation.selected_codes.length && !annotation.no_codes)} onClick={confirmIndependent}>{revealedBefore ? "Confirm codes & return to verification" : "Confirm codes & reveal AI"}</button>
          </div>
        </> : <>
          <h2>{complete ? "Verification complete" : "2. Verify AI annotations"}</h2>
          <p className="instructions">Accept or reject each AI subtheme you did not choose yourself. AI subthemes that match your own choices are agreed automatically.</p>
          <div className="your-codes-row">
            <details className="independent-codes">
              <summary>Your codes ({annotation.selected_codes.length})</summary>
              {annotation.no_codes ? <p>No applicable codes.</p> : <ul>{ordered.filter(code => selected.has(code.key)).map(code => <li key={code.key} className={code.kind === "broad" ? "" : "sub-item"}>{taxonomy.shortLabel(code)}</li>)}</ul>}
            </details>
            <button type="button" disabled={disabled || pending > 0} onClick={editCodes}>Edit my codes</button>
          </div>
          <p className="review-count">{reviewed} / {toReview.length} AI subthemes reviewed{agreedCodes.length > 0 && ` · ${agreedCodes.length} agreed automatically`}</p>
          <div className="ai-code-list">
            {agreedCodes.length > 0 && <details className="agreed-codes">
              <summary>✓ Matches your codes, no review needed ({agreedCodes.length})</summary>
              <ul>{agreedCodes.map(code => <li key={code.key}>
                <span className="code-kind">{code.group}</span>{taxonomy.fullLabel(code)}
              </li>)}</ul>
            </details>}
            {toReview.map(code => <div key={code.key} className="ai-review-row">
              <p><span className="code-kind">{code.group}</span>{taxonomy.fullLabel(code)}</p>
              <p className="help-text">You did not select this subtheme.</p>
              <div className="decision-buttons">
                <button disabled={disabled} aria-label={`Accept ${code.label}`} aria-pressed={annotation.ai_reviews[code.key] === "accepted"} onClick={() => review(code.key, "accepted")}>Accept</button>
                <button disabled={disabled} aria-label={`Reject ${code.label}`} aria-pressed={annotation.ai_reviews[code.key] === "rejected"} onClick={() => review(code.key, "rejected")}>Reject</button>
              </div>
            </div>)}
            {aiSelected.length === 0 && <p>The AI marked no subthemes for this post.</p>}
            {aiSelected.length > 0 && toReview.length === 0 && <p>All AI subthemes match your own choices. Nothing to review.</p>}
          </div>
          <div className="form-actions">
            {complete ? hasNext ? <button className="primary" disabled={pending > 0 || disabled} onClick={onNext}>Next unfinished post</button> : <p>All posts are complete.</p> : <button className="primary" disabled={disabled || pending > 0 || reviewed !== toReview.length} onClick={() => { setTransitioning(true); enqueue("complete", {}); }}>Complete verification</button>}
          </div>
        </>}
      </section>
    </div>
  </>;
}
