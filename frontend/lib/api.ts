export type User = { id: number; email: string; is_staff: boolean };
export type Code = { key: string; header: string; label: string; group: string; kind: "detailed" | "broad"; column: number };
export type Stage = "not_started" | "annotate" | "verify" | "complete";
export type RecordSummary = { id: number; external_id: string; source_row: number; title: string; subreddit: string; stage: Stage };
export type Post = Omit<RecordSummary, "stage"> & { text: string; ai_codes: string[] | null };
export type Annotation = {
  selected_codes: string[]; no_codes: boolean; ai_reviews: Record<string, "accepted" | "rejected">;
  notes: string; stage: "annotate" | "verify" | "complete"; revision: number;
  updated_at: string | null; independent_confirmed_at: string | null; completed_at: string | null;
};
export type Project = {
  dataset: { filename: string; record_count: number; code_count: number; workbook_synced: boolean; workbook_synced_at: string | null };
  codes: Code[]; records: RecordSummary[];
};
export type Detail = { record: Post; annotation: Annotation };
export type SaveResponse = { annotation: Annotation; workbook_synced: boolean; ai_codes: string[] | null };
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function api<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
  const csrf = document.cookie.split("; ").find(part => part.startsWith("csrftoken="))?.split("=")[1] || "";
  const response = await fetch(`/api${path.replace(/\/$/, "")}`, {
    method: body ? "POST" : "GET", credentials: "same-origin", cache: "no-store", signal,
    headers: body ? { "Content-Type": "application/json", "X-CSRFToken": decodeURIComponent(csrf) } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(data.error || (response.status === 403 ? "Your session needs refreshing. Reload this page and sign in again." : "The server could not finish this request. Please retry."), response.status);
  return data as T;
}

