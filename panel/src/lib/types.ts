export interface Denied {
  as_of: string;
  access: false;
}

export interface Capabilities {
  as_of: string;
  email: string;
  roles: string[];
  is_admin: boolean;
  views: Record<"ops" | "queries" | "telegram" | "review" | "costs" | "audit", boolean>;
  actions_enabled: boolean;
  actions_reason: string;
  not_instrumented: Array<{ capability: string; reason: string }>;
}

export interface Stage {
  stage: string;
  label: string;
  access: boolean;
  total?: number;
  pending?: number | null;
  running?: number | null;
  failed?: number | null;
  uncertain?: number | null;
  last_movement?: string | null;
  unit?: string;
}

export interface AttentionItem {
  severity: "critical" | "warning" | "info";
  kind: string;
  resource: string;
  resource_id: string | null;
  title: string;
  detail: string;
  since: string | null;
}

export interface Overview {
  as_of: string;
  access: true;
  stages: Stage[];
  attention: AttentionItem[];
  sources: { total: number; by_state: Record<string, number> };
  jobs: {
    ready: number;
    scheduled: number;
    leased: number;
    lease_expired: number;
    dead_letter: number;
    oldest_ready_at: string | null;
  };
}

export type Maybe<T> = (T & { access: true }) | Denied;

export interface SourceRow {
  id: string;
  code: string;
  name: string;
  authority: string;
  phase: string;
  state: string;
  state_reason: string | null;
  base_url: string | null;
  supported_objects: string[];
  owner: string | null;
  last_checked_at: string | null;
  last_success_at: string | null;
  poll_interval_seconds: number | null;
  policy_version: number | null;
  policy_reviewed_at: string | null;
  coverage_scopes: number;
  last_run: { id: string; status: string; mode: string; started_at: string; finished_at: string | null; error_code: string | null } | null;
  last_change_at: string | null;
  records: number;
}

export interface RunRow {
  id: string;
  source_code: string;
  source_name: string;
  mode: string;
  status: string;
  connector_version: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number;
  items_discovered: number;
  items_fetched: number;
  items_unchanged: number;
  items_normalized: number;
  items_quarantined: number;
  items_failed: number;
  error_code: string | null;
  trace_id: string;
  derived_jobs_open: number;
}

export interface QueueRow {
  family: string;
  ready: number;
  scheduled: number;
  leased: number;
  lease_expired: number;
  retry_wait: number;
  dead_letter: number;
  succeeded_24h: number;
  oldest_ready_at: string | null;
}

export interface JobRow {
  id: string;
  kind: string;
  state: string;
  attempts: number;
  max_attempts: number;
  run_at: string;
  lease_owner: string | null;
  lease_until: string | null;
  lease_expired: boolean;
  last_error_code: string | null;
  updated_at: string;
  finished_at: string | null;
}
