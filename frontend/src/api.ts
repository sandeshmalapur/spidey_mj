import type {
  HealthResponse,
  Item,
  ItemCreateParams,
  ItemUpdateParams,
  ItemState,
  ItemType,
  Priority,
  SyncResult,
  SyncStatus,
} from './types';

const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ||
  'http://127.0.0.1:8000';

async function request<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(options?.headers || {}),
      },
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : 'Network request failed';
    throw new Error(`Failed to connect to backend at ${url} (${msg})`);
  }

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) {
        if (typeof body.detail === 'string') {
          errorDetail = body.detail;
        } else if (Array.isArray(body.detail)) {
          errorDetail = body.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join('; ');
        } else {
          errorDetail = JSON.stringify(body.detail);
        }
      }
    } catch {
      // Ignore json parse error and keep default statusText
    }
    throw new Error(errorDetail);
  }

  return response.json() as Promise<T>;
}

export const api = {
  getHealth: () => request<HealthResponse>('/api/health'),

  getItems: (filters?: {
    priority?: Priority;
    state?: ItemState;
    item_type?: ItemType;
    include_snoozed?: boolean;
  }) => {
    const params = new URLSearchParams();
    if (filters?.priority) params.set('priority', filters.priority);
    if (filters?.state) params.set('state', filters.state);
    if (filters?.item_type) params.set('item_type', filters.item_type);
    if (filters?.include_snoozed) params.set('include_snoozed', 'true');
    const qs = params.toString();
    return request<Item[]>(`/api/items${qs ? `?${qs}` : ''}`);
  },

  getItem: (id: number) => request<Item>(`/api/items/${id}`),

  createItem: (data: ItemCreateParams) =>
    request<Item>('/api/items', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  ingestManual: (data: {
    source_title?: string;
    sender?: string;
    text: string;
    received_at?: string;
  }) =>
    request<{ duplicate: boolean; item: Item }>('/api/ingest/manual', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  patchItem: (id: number, data: ItemUpdateParams) =>
    request<Item>(`/api/items/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  acknowledgeItem: (id: number) =>
    request<Item>(`/api/items/${id}/acknowledge`, { method: 'POST' }),

  snoozeItem: (id: number, until: string) =>
    request<Item>(`/api/items/${id}/snooze`, {
      method: 'POST',
      body: JSON.stringify({ until }),
    }),

  startItem: (id: number) =>
    request<Item>(`/api/items/${id}/start`, { method: 'POST' }),

  completeItem: (id: number) =>
    request<Item>(`/api/items/${id}/complete`, { method: 'POST' }),

  cancelItem: (id: number) =>
    request<Item>(`/api/items/${id}/cancel`, { method: 'POST' }),

  reopenItem: (id: number) =>
    request<Item>(`/api/items/${id}/reopen`, { method: 'POST' }),

  seedDemo: () =>
    request<{ status: string; seeded: number; total_demo: number }>('/api/demo/seed', {
      method: 'POST',
    }),

  deleteDemo: () =>
    request<{ status: string; deleted: number }>('/api/demo', {
      method: 'DELETE',
    }),

  syncGmail: () =>
    request<SyncResult>('/api/sync/gmail', {
      method: 'POST',
    }),

  getSyncStatus: () => request<SyncStatus>('/api/sync/status'),

  reanalyzeItem: (id: number) =>
    request<Item>(`/api/items/${id}/reanalyze`, { method: 'POST' }),

  reanalyzeAll: () =>
    request<{
      status: string;
      message: string;
      counts: { total: number; reanalyzed: number; updated: number; errors: number };
    }>('/api/reanalyze', { method: 'POST' }),
};
