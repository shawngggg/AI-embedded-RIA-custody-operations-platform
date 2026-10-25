export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Options = { method?: string; body?: unknown; form?: FormData };

export async function api<T = any>(path: string, opts: Options = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const init: RequestInit = {
    method: opts.method ?? (opts.body !== undefined || opts.form ? "POST" : "GET"),
    credentials: "include",
    headers,
  };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    headers["Content-Type"] = "application/json";
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let message = r.statusText;
    try {
      const j = await r.json();
      message = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, message);
  }
  return r.json() as Promise<T>;
}

export type User = {
  id: number; username: string; display_name: string; email: string | null; role: string; role_label: string;
  active: boolean; is_demo: boolean; note: string | null; workspace: "workbench" | "portal";
  firm: { id: number; crd: string; name: string } | null; client_account_id: string | null;
  registrations: { code: string; label: string }[]; individual_crd: string | null; can_accept_accounts: boolean;
  team: string | null; team_label: string | null; capabilities: Record<string, boolean>;
};

export type Check = { check: string; result: "pass" | "fail" | "manual"; detail: string };

export type Doc = {
  id: number; case_id: number; doc_type: string; type_label: string; filename: string; content_type: string;
  size: number; sha256: string; channel: string; channel_label: string; received_on: string; uploaded_by: string;
  uploaded_at: string | null; status: "received" | "in_good_order" | "not_in_good_order" | "superseded"; reviewed_by: string | null;
  reviewed_at: string | null; review_note: string | null; checks: Check[]; checks_passed: boolean; synthetic: boolean;
  reopen_refusal?: string | null;
};

export type Step = { lane: string; step: string; outcome: string; detail: string[]; rule_ids: string[] };

export type CaseSummary = {
  id: number; kind: string; title: string; status: string; queue: string | null; queue_label: string | null;
  firm: string | null; account_id: string | null; target_id: string; created_at: string; updated_at: string;
  queue_entered_at: string | null; path: string | null;
  sla: { due: string | null; breached: boolean; hours_left: number | null };
};

export type Restriction = {
  id: number; target_id: string; code: string; description: string; owner: string; reason: string;
  source: string; case_id: number | null; placed_by: string; placed_at: string; active: boolean;
  removed_by: string | null; removed_at: string | null; removal_reason: string | null;
  authorization: string | null; blocks: string[]; requires_authorization: boolean;
};

export type Decision = {
  id: number; case_id: number; decision_type: string; label: string; value: string; value_label: string;
  reference: string; note: string | null; status: string; proposed_by: string; proposed_by_id: number;
  proposed_at: string; decided_by: string | null; decided_at: string | null; approver_roles: string[];
  can_approve?: boolean; case_title?: string; documents?: DocRef[];
};
export type DocRef = { id: number; type_label: string; filename: string; status: Doc["status"] };

export type Correction = {
  field: string; label: string; type: string; rule_id: string; reason: string; current: any;
  doc_types: { value: string; label: string }[]; documents: Doc[];
  options?: { value: string; label: string }[]; people?: { name: string; value: string | number }[];
};

export type CaseDetail = CaseSummary & {
  related_documents?: { title: string; case_id: number; documents: Doc[] } | null;
  application: any; decisions: any; result: any; extra: any; restrictions: Restriction[];
  decision_log: Decision[]; corrections: Correction[]; manual_steps: number; nigo_count: number;
  available_decisions: { decision_type: string; label: string; values: { value: string; label: string }[];
    approver_roles: string[] }[];
  account: { id: string; status: string; group: string; display_name: string } | null; closed_at: string | null;
  acceptance: { can_accept: boolean; reason: string | null; signs_as: string | null; to_review: number[];
    opened: number[] } | null;
  documents: Doc[];
  rescission: { can_request: boolean; reason: string | null; kinds: Record<string, string>; return_to: Record<string, string> } | null;
  rework: { reason: string; reference: string; requested_by: string; approved_by: string | null; at: string;
    kind?: "send_back"; documents?: { id: number; label: string; filename: string }[] } | null;
  ai_suggestions: AiSuggestion[];
  identity_check: IdentityCheckState | null;
};

/** A failed identity check (app/identity_check.py): back to the RIA to resubmit, or to AML compliance. */
export type IdentityIssue = { kind: string; person: string; note: string; status: string; by: string; at: string;
  resubmitted_at?: string };
export type IdentityCheckState = { can_record: boolean; people: string[]; kinds: Record<string, string>;
  issue: IdentityIssue | null; history: IdentityIssue[] };

/** A value the AI read from the package, and what a person in onboarding did with it (app/ai_suggestions.py). */
export type AiSuggestion = {
  id: number; field: string; label: string; kind: "text" | "number" | "date" | "boolean" | "person";
  team: "onboarding" | "cip"; team_label: string;
  choices: string[] | null; suggested: any; suggested_text: string; quote: string; mode: string;
  status: "pending" | "accepted" | "edited" | "rejected"; final: any; final_text: string | null; reason: string | null;
  reviewed_by: string | null; reviewed_at: string | null; actions: ("accept" | "edit" | "reject")[];
};

/** The AI suggestion acceptance metric: live suggestions counted, illustrative ones tracked apart. */
export type Tally = { suggestions: number; reviewed: number; pending: number; accepted: number; edited: number;
  rejected: number; share_accepted: number | null; cases: number };
export type SuggestionSummary = { live: Tally; illustrative: Tally; by_field: (Tally & { field: string; label: string })[];
  by_prompt: (Tally & { prompt_version: string | null; model: string | null })[];
  recent_changes?: { id: number; case_id: number | null; case: string; mode: string; field: string; label: string;
    status: string; suggested: string; final: string | null; reason: string | null; by: string; at: string }[] };

/** What RIA users and clients get for a case (app/portal_view.py): none of the custodian's internal work. */
export type PortalCase = Pick<CaseSummary, "id" | "kind" | "title" | "status" | "firm" | "account_id" | "target_id" | "created_at" | "updated_at"> & {
  portal: true; status_note: string; requested_items: string[]; corrections: Correction[]; documents: Doc[];
  restrictions_portal: { reason: string; limits: string[] }[];
  account: { id: string; status: string; group: string; display_name: string } | null;
  details: { label: string; value: string }[]; history: { status: string; at: string }[]; closed_at: string | null;
};
