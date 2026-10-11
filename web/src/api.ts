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
  can_approve?: boolean; case_title?: string;
};

export type Correction = { field: string; label: string; type: string; rule_id: string; reason: string; current: any };

export type CaseDetail = CaseSummary & {
  application: any; decisions: any; result: any; extra: any; restrictions: Restriction[];
  decision_log: Decision[]; corrections: Correction[]; manual_steps: number; nigo_count: number;
  available_decisions: { decision_type: string; label: string; values: { value: string; label: string }[];
    approver_roles: string[] }[];
  account: { id: string; status: string; group: string; display_name: string } | null; closed_at: string | null;
};
