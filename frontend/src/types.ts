export type ItemType = 'information' | 'task';

export type ItemKind =
  | 'assignment'
  | 'assessment'
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
  duration_minutes?: number | null;
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
  source_status?: string | null;
  deadline_changed_at?: string | null;
  next_reminder_at?: string | null;
  next_reminder_kind?: string | null;
  extraction_provider?: string | null;
}

export interface SyncCounts {
  fetched: number;
  created: number;
  updated: number;
  skipped: number;
  errors: number;
}

export interface ClassroomSyncCounts {
  courses: number;
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
  // Classroom status
  classroom_status?: 'idle' | 'success' | 'error' | 'running' | 'reconnect_needed';
  classroom_last_synced_at?: string | null;
  classroom_counts?: ClassroomSyncCounts;
  classroom_error_message?: string | null;
  classroom_reconnect_command?: string | null;
}

export interface SyncResult {
  status: 'success' | 'error' | 'skipped';
  message: string;
  counts: SyncCounts;
  error_message?: string | null;
}

export interface ClassroomSyncResult {
  status: 'success' | 'error' | 'skipped' | 'reconnect_needed';
  message: string;
  counts: ClassroomSyncCounts;
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
  duration_minutes?: number | null;
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
  duration_minutes?: number | null;
  kind?: ItemKind;
  item_type?: ItemType;
}

export interface NotificationLogEntry {
  id: number;
  item_id: number | null;
  at: string;
  channel: 'ntfy' | 'local';
  title: string;
  body: string;
  outcome: string;
  error?: string | null;
  scheduled_for?: string | null;
  delay_seconds?: number | null;
}

export interface Reminder {
  id: number;
  item_id: number;
  kind: string;
  scheduled_for: string;
  status: string;
  dedupe_key?: string | null;
  reason?: string | null;
  created_at: string;
  sent_at?: string | null;
}

export interface NotificationStatus {
  ntfy_configured: boolean;
  notifications_enabled: boolean;
  quiet_hours_enabled: boolean;
  quiet_hours_start: string;
  quiet_hours_end: string;
  urgent_bypass_quiet: boolean;
  max_per_hour: number;
  recent_count: number;
}

export interface OllamaStatusResponse {
  provider: string;
  reachable: boolean;
  model_present: boolean;
  configured_model: string;
  url: string;
  available_models?: string[];
  error?: string | null;
}

export interface SarvamVoiceStatusResponse {
  enabled: boolean;
  configured: boolean;
  max_calls_per_hour: number;
  calls_in_last_hour: number;
}

export interface SystemStatusResponse {
  database_ok: boolean;
  gmail_token_present: boolean;
  classroom_token_present: boolean;
  ntfy_configured: boolean;
  scheduler_running: boolean;
  auto_sync_enabled: boolean;
  last_sync_gmail?: string | null;
  last_sync_classroom?: string | null;
  version: string;
  ollama?: OllamaStatusResponse | null;
}

export interface AuthMeResponse {
  authenticated: boolean;
  auth_enabled: boolean;
  email: string | null;
  display_name: string | null;
}

export interface SourceConnection {
  connected: boolean;
  scopes_granted: string[];
  last_sync: string | null;
  needs_reconnect: boolean;
}

export interface ConnectionsResponse {
  gmail: SourceConnection;
  classroom: SourceConnection;
}



