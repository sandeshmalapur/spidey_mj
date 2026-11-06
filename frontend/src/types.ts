export type ItemType = 'information' | 'task';

export type ItemKind =
  | 'assignment'
  | 'announcement'
  | 'recruiter'
  | 'interview'
  | 'document_request'
  | 'event'
  | 'meeting'
  | 'routine'
  | 'other';

export type Priority = 'normal' | 'important' | 'urgent';

export type ItemState =
  | 'needs_review'
  | 'not_started'
  | 'in_progress'
  | 'completed'
  | 'cancelled';

export type DeadlineStatus = 'none' | 'confirmed' | 'ambiguous';

export type SourceType = 'manual' | 'gmail' | 'classroom' | 'demo';

export interface Item {
  id: number;
  title: string;
  item_type: ItemType;
  kind: ItemKind;
  summary: string | null;
  priority: Priority;
  priority_reason: string | null;
  priority_override: Priority | null;
  effective_priority: Priority;
  extraction_confidence: number;
  state: ItemState;
  acknowledged_at: string | null;
  snoozed_until: string | null;
  deadline_text: string | null;
  deadline_at: string | null;
  deadline_status: DeadlineStatus;
  links: string[];
  source_type: SourceType;
  source_id: string | null;
  thread_id?: string | null;
  source_title: string | null;
  sender: string | null;
  received_at: string | null;
  evidence: string | null;
  is_demo: boolean;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface SyncCounts {
  fetched: number;
  created: number;
  updated: number;
  skipped: number;
  errors: number;
}

export interface SyncStatus {
  source_type: string;
  last_synced_at: string | null;
  next_run_at: string | null;
  status: 'idle' | 'success' | 'error' | 'running' | 'reconnect_needed';
  counts: SyncCounts;
  error_message: string | null;
  auto_sync_enabled: boolean;
  interval_minutes: number;
  reconnect_command: string | null;
  consecutive_errors?: number;
  backoff_minutes?: number | null;
}

export interface SyncResult {
  status: 'success' | 'error' | 'skipped';
  message: string;
  counts: SyncCounts;
  error_message?: string | null;
}

export interface HealthResponse {
  status: string;
  db: string;
  version: string;
}

export interface ItemCreateParams {
  title: string;
  item_type?: ItemType;
  kind?: ItemKind;
  summary?: string | null;
  priority?: Priority;
  priority_reason?: string | null;
  priority_override?: Priority | null;
  extraction_confidence?: number;
  state?: ItemState;
  deadline_text?: string | null;
  deadline_at?: string | null;
  deadline_status?: DeadlineStatus;
  links?: string[];
  source_type?: SourceType;
  source_id?: string | null;
  source_title?: string | null;
  sender?: string | null;
  received_at?: string | null;
  evidence?: string | null;
  is_demo?: boolean;
}

export interface ItemUpdateParams {
  title?: string;
  summary?: string | null;
  deadline_text?: string | null;
  deadline_at?: string | null;
  deadline_status?: DeadlineStatus;
  priority_override?: Priority | null;
  kind?: ItemKind;
  item_type?: ItemType;
}

