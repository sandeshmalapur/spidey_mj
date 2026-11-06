import React, { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import type {
  HealthResponse,
  Item,
  ItemKind,
  ItemState,
  ItemType,
  ItemUpdateParams,
  Priority,
  SyncStatus,
} from './types';
import './App.css';

const localTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;

function formatDateTimeWithTz(dateStr: string | null | undefined): string {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return String(dateStr);
  try {
    return d.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      timeZoneName: 'short',
    });
  } catch {
    return d.toLocaleString();
  }
}

function formatKind(kind: string): string {
  const map: Record<string, string> = {
    assignment: 'Assignment',
    announcement: 'Announcement',
    recruiter: 'Recruiter',
    interview: 'Interview',
    document_request: 'Document request',
    event: 'Event',
    meeting: 'Meeting',
    routine: 'Routine',
    other: 'Other',
  };
  return map[kind] || kind.replace(/_/g, ' ');
}

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);

  const [items, setItems] = useState<Item[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  // Filters
  const [priorityFilter, setPriorityFilter] = useState<'all' | Priority>('all');
  const [typeFilter, setTypeFilter] = useState<'all' | ItemType>('all');
  const [stateFilter, setStateFilter] = useState<'all' | ItemState>('all');
  const [showSnoozed, setShowSnoozed] = useState<boolean>(false);

  // Modals / Panels
  const [selectedItem, setSelectedItem] = useState<Item | null>(null);
  const [editingItem, setEditingItem] = useState<Item | null>(null);
  const [showPasteModal, setShowPasteModal] = useState<boolean>(false);

  // Edit form state
  const [editTitle, setEditTitle] = useState('');
  const [editDeadlineText, setEditDeadlineText] = useState('');
  const [editDeadlineAt, setEditDeadlineAt] = useState('');
  const [editPriorityOverride, setEditPriorityOverride] = useState<string>('none');
  const [editKind, setEditKind] = useState<ItemKind>('other');
  const [editItemType, setEditItemType] = useState<ItemType>('task');

  // Manual paste state
  const [pasteSourceTitle, setPasteSourceTitle] = useState('');
  const [pasteSender, setPasteSender] = useState('');
  const [pasteEvidence, setPasteEvidence] = useState('');
  const [pasteSubmitting, setPasteSubmitting] = useState(false);

  // Gmail sync state
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [syncing, setSyncing] = useState<boolean>(false);
  const [syncError, setSyncError] = useState<string | null>(null);

  // Check health
  const checkHealth = async () => {
    try {
      const h = await api.getHealth();
      setHealth(h);
      setHealthError(null);
    } catch (err: unknown) {
      setHealth(null);
      setHealthError(err instanceof Error ? err.message : 'Backend unavailable');
    }
  };

  // Load sync status
  const loadSyncStatus = async () => {
    try {
      const s = await api.getSyncStatus();
      setSyncStatus(s);
      if (s.status === 'error' && s.error_message) {
        setSyncError(s.error_message);
      }
    } catch {
      // Ignore initial sync status check error
    }
  };

  // Trigger Gmail Sync
  const handleSyncGmail = async () => {
    setSyncing(true);
    setSyncError(null);
    try {
      const res = await api.syncGmail();
      if (res.status === 'error') {
        setSyncError(res.error_message || res.message);
      } else {
        setActionSuccess(res.message);
        setTimeout(() => setActionSuccess(null), 5000);
      }
      await loadSyncStatus();
      await loadItems();
    } catch (err: unknown) {
      setSyncError(err instanceof Error ? err.message : 'Gmail sync failed');
    } finally {
      setSyncing(false);
    }
  };

  // Load items
  const loadItems = async () => {
    setLoading(true);
    setErrorMessage(null);
    try {
      // Fetch all items with include_snoozed=true so we can compute accurate tab counts
      const data = await api.getItems({ include_snoozed: true });
      setItems(data);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to fetch items');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    checkHealth();
    loadItems();
    loadSyncStatus();
  }, []);

  const hasDemoItems = useMemo(() => items.some((i) => i.is_demo), [items]);

  // Tab counts based on effective_priority
  const counts = useMemo(() => {
    const isVisible = (item: Item) => {
      if (showSnoozed) return true;
      if (!item.snoozed_until) return true;
      return new Date(item.snoozed_until).getTime() <= Date.now();
    };

    const visibleItems = items.filter(isVisible);

    return {
      all: visibleItems.length,
      urgent: visibleItems.filter((i) => i.effective_priority === 'urgent').length,
      important: visibleItems.filter((i) => i.effective_priority === 'important').length,
      normal: visibleItems.filter((i) => i.effective_priority === 'normal').length,
    };
  }, [items, showSnoozed]);

  // Filtered and sorted items
  const displayedItems = useMemo(() => {
    let result = items.filter((item) => {
      // Snoozed filter
      if (!showSnoozed && item.snoozed_until) {
        if (new Date(item.snoozed_until).getTime() > Date.now()) {
          return false;
        }
      }

      // Priority tab filter
      if (priorityFilter !== 'all' && item.effective_priority !== priorityFilter) {
        return false;
      }

      // Type filter
      if (typeFilter !== 'all' && item.item_type !== typeFilter) {
        return false;
      }

      // State filter
      if (stateFilter !== 'all' && item.state !== stateFilter) {
        return false;
      }

      return true;
    });

    // Default sort: urgent first, then nearest deadline, then newest
    const priorityWeight: Record<Priority, number> = {
      urgent: 1,
      important: 2,
      normal: 3,
    };

    result.sort((a, b) => {
      const pA = priorityWeight[a.effective_priority] || 99;
      const pB = priorityWeight[b.effective_priority] || 99;
      if (pA !== pB) return pA - pB;

      if (a.deadline_at && b.deadline_at) {
        const diff = new Date(a.deadline_at).getTime() - new Date(b.deadline_at).getTime();
        if (diff !== 0) return diff;
      } else if (a.deadline_at && !b.deadline_at) {
        return -1;
      } else if (!a.deadline_at && b.deadline_at) {
        return 1;
      }

      return b.id - a.id;
    });

    return result;
  }, [items, priorityFilter, typeFilter, stateFilter, showSnoozed]);

  // Actions
  const runAction = async (actionFn: () => Promise<Item>, msg: string) => {
    try {
      setErrorMessage(null);
      const updated = await actionFn();
      setActionSuccess(msg);
      setTimeout(() => setActionSuccess(null), 3000);
      await loadItems();
      if (selectedItem && selectedItem.id === updated.id) {
        setSelectedItem(updated);
      }
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Action failed');
    }
  };

  const handleSnooze = (item: Item, hours: number | 'tomorrow') => {
    let untilDate: Date;
    if (hours === 'tomorrow') {
      untilDate = new Date();
      untilDate.setDate(untilDate.getDate() + 1);
      untilDate.setHours(9, 0, 0, 0);
    } else {
      untilDate = new Date(Date.now() + hours * 3600 * 1000);
    }
    const iso = untilDate.toISOString();
    runAction(() => api.snoozeItem(item.id, iso), `Snoozed item #${item.id}`);
  };

  // Demo data handlers
  const handleSeedDemo = async () => {
    try {
      setErrorMessage(null);
      const res = await api.seedDemo();
      setActionSuccess(`Demo items loaded (${res.seeded} seeded, total demo: ${res.total_demo})`);
      setTimeout(() => setActionSuccess(null), 3000);
      await loadItems();
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to seed demo data');
    }
  };

  const handleDeleteDemo = async () => {
    try {
      setErrorMessage(null);
      const res = await api.deleteDemo();
      setActionSuccess(`Cleared demo data (${res.deleted} deleted)`);
      setTimeout(() => setActionSuccess(null), 3000);
      await loadItems();
      if (selectedItem?.is_demo) setSelectedItem(null);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to clear demo data');
    }
  };

  // Open Edit Form
  const openEdit = (item: Item) => {
    setEditingItem(item);
    setEditTitle(item.title);
    setEditDeadlineText(item.deadline_text || '');
    setEditKind(item.kind);
    setEditItemType(item.item_type);
    setEditPriorityOverride(item.priority_override || 'none');

    if (item.deadline_at) {
      const d = new Date(item.deadline_at);
      if (!isNaN(d.getTime())) {
        const pad = (n: number) => String(n).padStart(2, '0');
        const formatted = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
        setEditDeadlineAt(formatted);
      } else {
        setEditDeadlineAt('');
      }
    } else {
      setEditDeadlineAt('');
    }
  };

  const handleSaveEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingItem) return;

    let deadlineAtIso: string | null = null;
    if (editDeadlineAt.trim()) {
      const parsed = new Date(editDeadlineAt);
      if (!isNaN(parsed.getTime())) {
        deadlineAtIso = parsed.toISOString();
      }
    }

    const payload: ItemUpdateParams = {
      title: editTitle.trim(),
      deadline_text: editDeadlineText.trim() || null,
      deadline_at: deadlineAtIso,
      priority_override: editPriorityOverride === 'none' ? null : (editPriorityOverride as Priority),
      kind: editKind,
      item_type: editItemType,
    };

    try {
      const updated = await api.patchItem(editingItem.id, payload);
      setEditingItem(null);
      setActionSuccess(`Updated item #${updated.id}`);
      setTimeout(() => setActionSuccess(null), 3000);
      await loadItems();
      if (selectedItem?.id === updated.id) {
        setSelectedItem(updated);
      }
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to update item');
    }
  };

  // Handle Manual Paste Submission (Milestone 4: runs rules extraction & priority engine)
  const handleManualSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pasteEvidence.trim()) {
      setErrorMessage('Pasted text is required.');
      return;
    }

    setPasteSubmitting(true);
    setErrorMessage(null);
    try {
      const res = await api.ingestManual({
        source_title: pasteSourceTitle.trim() || undefined,
        sender: pasteSender.trim() || undefined,
        text: pasteEvidence.trim(),
      });

      setShowPasteModal(false);
      setPasteSourceTitle('');
      setPasteSender('');
      setPasteEvidence('');
      if (res.duplicate) {
        setActionSuccess(`Already imported: "${res.item.title}"`);
      } else {
        setActionSuccess(`Imported & analyzed: "${res.item.title}" (${res.item.priority.toUpperCase()})`);
      }
      setTimeout(() => setActionSuccess(null), 4000);
      await loadItems();
      setSelectedItem(res.item);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to ingest manual item');
    } finally {
      setPasteSubmitting(false);
    }
  };

  // Helper formatting for deadlines
  const formatDeadline = (item: Item) => {
    if (item.deadline_status === 'confirmed' && item.deadline_at) {
      const formatted = formatDateTimeWithTz(item.deadline_at);
      return item.deadline_text && item.deadline_text.trim()
        ? `${item.deadline_text.trim()} (${formatted})`
        : formatted;
    }
    const cleanText = item.deadline_text?.trim();
    if (cleanText && cleanText.toLowerCase() !== 'deadline') {
      return cleanText;
    }
    return 'No deadline';
  };

  return (
    <div className="app-container">
      {/* Header */}
      <header className="app-header">
        <div className="header-brand">
          <h1>ClassMail J</h1>
          <span className="header-subtitle">Local AI Email &amp; Classroom Assistant</span>
        </div>

        <div className="header-status">
          {health ? (
            <div className="health-pill success" title={`API v${health.version}, DB: ${health.db}`}>
              <span className="dot dot-success"></span>
              <span>Backend Online ({health.db})</span>
            </div>
          ) : (
            <div className="health-pill error" title={healthError || 'Offline'}>
              <span className="dot dot-error"></span>
              <span>Backend Offline</span>
            </div>
          )}
          <button className="btn btn-secondary btn-sm" onClick={checkHealth}>
            Check Health
          </button>
        </div>
      </header>

      {/* Demo Banner */}
      {hasDemoItems && (
        <div className="demo-banner">
          <span className="demo-banner-icon">ℹ️</span>
          <span>
            <strong>Demo data shown.</strong> Not from your real accounts.
          </span>
        </div>
      )}

      {/* Alert Messages */}
      {actionSuccess && <div className="alert alert-success">{actionSuccess}</div>}
      {errorMessage && (
        <div className="alert alert-error">
          <span>{errorMessage}</span>
          <button className="alert-close" onClick={() => setErrorMessage(null)}>
            ×
          </button>
        </div>
      )}

      {/* Top Action Toolbar */}
      <section className="toolbar">
        <div className="toolbar-left">
          <button className="btn btn-primary" onClick={() => setShowPasteModal(true)}>
            + Paste Email / Text
          </button>
          <button
            className="btn btn-gmail"
            onClick={handleSyncGmail}
            disabled={syncing}
            title="Fetch recent messages from your Gmail inbox"
          >
            {syncing ? '⏳ Syncing Gmail...' : '📥 Sync Gmail now'}
          </button>
        </div>
        <div className="toolbar-right">
          <button className="btn btn-outline" onClick={handleSeedDemo}>
            Load Demo Data
          </button>
          <button className="btn btn-outline btn-danger" onClick={handleDeleteDemo}>
            Clear Demo Data
          </button>
          <button className="btn btn-secondary" onClick={loadItems} disabled={loading}>
            {loading ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
      </section>

      {/* Gmail Sync Error & Status Info */}
      {syncError && (
        <div className="sync-error-banner">
          <span className="sync-error-icon">⚠️</span>
          <div className="sync-error-content">
            <strong>Gmail Sync Note:</strong> {syncError}
          </div>
          <button className="alert-close" onClick={() => setSyncError(null)}>
            ×
          </button>
        </div>
      )}
      {syncStatus && syncStatus.last_synced_at && (
        <div className="sync-info-bar">
          <span>
            <strong>Gmail Sync:</strong> Last synced at {formatDateTimeWithTz(syncStatus.last_synced_at)}
          </span>
          <span>
            Fetched: {syncStatus.counts.fetched} | Created: {syncStatus.counts.created} | Updated: {syncStatus.counts.updated} | Skipped: {syncStatus.counts.skipped}
            {syncStatus.counts.errors > 0 ? ` | Errors: ${syncStatus.counts.errors}` : ''}
          </span>
        </div>
      )}

      {/* Primary Priority Filter Tabs */}
      <nav className="filter-tabs" aria-label="Priority Tabs">
        <button
          className={`tab-btn ${priorityFilter === 'all' ? 'active' : ''}`}
          onClick={() => setPriorityFilter('all')}
        >
          All <span className="tab-badge">{counts.all}</span>
        </button>
        <button
          className={`tab-btn tab-urgent ${priorityFilter === 'urgent' ? 'active' : ''}`}
          onClick={() => setPriorityFilter('urgent')}
        >
          Urgent <span className="tab-badge">{counts.urgent}</span>
        </button>
        <button
          className={`tab-btn tab-important ${priorityFilter === 'important' ? 'active' : ''}`}
          onClick={() => setPriorityFilter('important')}
        >
          Important <span className="tab-badge">{counts.important}</span>
        </button>
        <button
          className={`tab-btn tab-normal ${priorityFilter === 'normal' ? 'active' : ''}`}
          onClick={() => setPriorityFilter('normal')}
        >
          Normal <span className="tab-badge">{counts.normal}</span>
        </button>
      </nav>

      {/* Secondary Controls */}
      <section className="secondary-filters">
        <div className="filter-group">
          <label htmlFor="type-filter">Type:</label>
          <select
            id="type-filter"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as 'all' | ItemType)}
          >
            <option value="all">All Types</option>
            <option value="task">Tasks</option>
            <option value="information">Information</option>
          </select>
        </div>

        <div className="filter-group">
          <label htmlFor="state-filter">State:</label>
          <select
            id="state-filter"
            value={stateFilter}
            onChange={(e) => setStateFilter(e.target.value as 'all' | ItemState)}
          >
            <option value="all">All States</option>
            <option value="needs_review">Needs Review</option>
            <option value="not_started">Not Started</option>
            <option value="in_progress">In Progress</option>
            <option value="completed">Completed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </div>

        <div className="filter-toggle">
          <label>
            <input
              type="checkbox"
              checked={showSnoozed}
              onChange={(e) => setShowSnoozed(e.target.checked)}
            />
            Show Snoozed Items
          </label>
        </div>
      </section>

      {/* Main Content Layout */}
      <div className="dashboard-layout">
        {/* Items List */}
        <main className="items-column">
          {loading && items.length === 0 && (
            <div className="empty-state">
              <p>Loading items...</p>
            </div>
          )}

          {!loading && displayedItems.length === 0 && (
            <div className="empty-state">
              <h3>No items found</h3>
              <p>Try selecting a different filter tab or load demo fixtures.</p>
              <button className="btn btn-primary" onClick={handleSeedDemo}>
                Load Demo Data
              </button>
            </div>
          )}

          <div className="cards-grid">
            {displayedItems.map((item) => {
              const isSelected = selectedItem?.id === item.id;
              const isSnoozed =
                item.snoozed_until && new Date(item.snoozed_until).getTime() > Date.now();

              return (
                <article
                  key={item.id}
                  className={`item-card priority-${item.effective_priority} ${
                    isSelected ? 'selected' : ''
                  } ${item.state === 'completed' ? 'item-completed' : ''}`}
                  onClick={() => setSelectedItem(item)}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setSelectedItem(item);
                    }
                  }}
                >
                  <div className="card-top">
                    <div className="card-badges">
                      <span className={`badge badge-priority badge-${item.effective_priority}`}>
                        {item.effective_priority.toUpperCase()}
                      </span>
                      {item.priority_override && (
                        <span className="badge badge-override" title="Overridden by user">
                          Override
                        </span>
                      )}
                      <span className="badge badge-kind">{formatKind(item.kind)}</span>
                      <span className="badge badge-state">{item.state.replace('_', ' ')}</span>
                      {item.source_type === 'gmail' && (
                        <span className="badge badge-gmail" title="Imported from Gmail">
                          ✉️ Gmail
                        </span>
                      )}
                      {item.is_demo && <span className="badge badge-demo">DEMO</span>}
                      {item.acknowledged_at && (
                        <span className="badge badge-ack">✓ Acknowledged</span>
                      )}
                      {isSnoozed && <span className="badge badge-snooze">⏰ Snoozed</span>}
                    </div>

                    <div className="card-actions-quick" onClick={(e) => e.stopPropagation()}>
                      <button
                        className="btn btn-outline btn-xs"
                        onClick={() => openEdit(item)}
                        title="Edit item"
                      >
                        Edit
                      </button>
                    </div>
                  </div>

                  <h3 className="card-title">{item.title}</h3>

                  {item.summary && <p className="card-summary">{item.summary}</p>}

                  {item.priority_reason && (
                    <div className="card-priority-reason">
                      <strong>Priority Note:</strong> {item.priority_reason}
                    </div>
                  )}

                  <div className="card-footer">
                    <div className="card-deadline">
                      <span className="deadline-label">📅 {formatDeadline(item)}</span>
                      {item.deadline_status === 'ambiguous' && (
                        <span className="badge badge-warning">Needs confirmation</span>
                      )}
                    </div>

                    <div className="card-source-area" onClick={(e) => e.stopPropagation()}>
                      {item.source_type === 'gmail' && item.source_id && (
                        <a
                          href={`https://mail.google.com/mail/u/0/#inbox/${item.source_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="gmail-source-link"
                          title="Open original message in Gmail"
                        >
                          ✉️ View in Gmail ↗
                        </a>
                      )}
                      {item.source_title && (
                        <span className="card-source">{item.source_title}</span>
                      )}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </main>

        {/* Detail Panel */}
        {selectedItem && (() => {
          const isClosed = selectedItem.state === 'completed' || selectedItem.state === 'cancelled';
          return (
            <aside className="detail-panel">
              <div className="panel-header">
                <h2>Item Details</h2>
                <button
                  className="btn-icon"
                  onClick={() => setSelectedItem(null)}
                  aria-label="Close detail panel"
                >
                  ✕
                </button>
              </div>

              <div className="panel-content">
                <div className="panel-title-area">
                  <h3>{selectedItem.title}</h3>
                  <div className="card-badges">
                    <span
                      className={`badge badge-priority badge-${selectedItem.effective_priority}`}
                    >
                      {selectedItem.effective_priority.toUpperCase()}
                    </span>
                    <span className="badge badge-kind">{formatKind(selectedItem.kind)}</span>
                    <span className="badge badge-state">{selectedItem.state.replace('_', ' ')}</span>
                    {selectedItem.is_demo && <span className="badge badge-demo">DEMO</span>}
                    {selectedItem.acknowledged_at && (
                      <span className="badge badge-ack">✓ Acknowledged</span>
                    )}
                    {selectedItem.snoozed_until && (
                      <span className="badge badge-snooze">⏰ Snoozed</span>
                    )}
                  </div>
                </div>

                {/* Action Buttons */}
                <div className="action-button-group">
                  {!isClosed && !selectedItem.acknowledged_at && (
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() =>
                        runAction(
                          () => api.acknowledgeItem(selectedItem.id),
                          `Acknowledged item #${selectedItem.id}`
                        )
                      }
                    >
                      Acknowledge
                    </button>
                  )}

                  {/* Snooze Presets - Hidden for completed/cancelled */}
                  {!isClosed && (
                    <div className="snooze-dropdown">
                      <span className="dropdown-label">Snooze:</span>
                      <button
                        className="btn btn-outline btn-xs"
                        onClick={() => handleSnooze(selectedItem, 1)}
                      >
                        1h
                      </button>
                      <button
                        className="btn btn-outline btn-xs"
                        onClick={() => handleSnooze(selectedItem, 4)}
                      >
                        4h
                      </button>
                      <button
                        className="btn btn-outline btn-xs"
                        onClick={() => handleSnooze(selectedItem, 'tomorrow')}
                      >
                        Tomorrow 9am
                      </button>
                    </div>
                  )}

                  {/* State Transitions */}
                  {(selectedItem.state === 'needs_review' ||
                    selectedItem.state === 'not_started') && (
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={() =>
                        runAction(
                          () => api.startItem(selectedItem.id),
                          `Started item #${selectedItem.id}`
                        )
                      }
                    >
                      Start
                    </button>
                  )}

                  {(selectedItem.state === 'not_started' ||
                    selectedItem.state === 'in_progress') && (
                    <button
                      className="btn btn-success btn-sm"
                      onClick={() =>
                        runAction(
                          () => api.completeItem(selectedItem.id),
                          `Completed item #${selectedItem.id}`
                        )
                      }
                    >
                      Complete
                    </button>
                  )}

                  {selectedItem.state !== 'completed' && selectedItem.state !== 'cancelled' && (
                    <button
                      className="btn btn-outline btn-danger btn-sm"
                      onClick={() =>
                        runAction(
                          () => api.cancelItem(selectedItem.id),
                          `Cancelled item #${selectedItem.id}`
                        )
                      }
                    >
                      Cancel
                    </button>
                  )}

                  {(selectedItem.state === 'completed' || selectedItem.state === 'cancelled') && (
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() =>
                        runAction(
                          () => api.reopenItem(selectedItem.id),
                          `Reopened item #${selectedItem.id}`
                        )
                      }
                    >
                      Reopen
                    </button>
                  )}

                  <button
                    className="btn btn-outline btn-sm"
                    onClick={() => openEdit(selectedItem)}
                  >
                    Edit Item
                  </button>
                </div>

                {/* Detail fields */}
                <div className="detail-metadata">
                  <p>
                    <strong>Type:</strong> {selectedItem.item_type}
                  </p>
                  <p>
                    <strong>Kind:</strong> {formatKind(selectedItem.kind)}
                  </p>
                  <p>
                    <strong>Confidence:</strong> {(selectedItem.extraction_confidence * 100).toFixed(0)}%
                  </p>
                  <p>
                    <strong>Priority:</strong> {selectedItem.priority}
                    {selectedItem.priority_override && (
                      <span className="override-indicator">
                        {' '}
                        (Override: {selectedItem.priority_override})
                      </span>
                    )}
                  </p>
                  {selectedItem.priority_reason && (
                    <p>
                      <strong>Priority Reason:</strong> {selectedItem.priority_reason}
                    </p>
                  )}
                  <p>
                    <strong>Deadline Text:</strong>{' '}
                    {selectedItem.deadline_text &&
                    selectedItem.deadline_text.trim() &&
                    selectedItem.deadline_text.trim().toLowerCase() !== 'deadline'
                      ? selectedItem.deadline_text.trim()
                      : 'No deadline'}
                  </p>
                  <p>
                    <strong>Deadline Status:</strong> {selectedItem.deadline_status}
                    {selectedItem.deadline_status === 'ambiguous' && (
                      <span className="badge badge-warning"> Needs confirmation</span>
                    )}
                  </p>
                {selectedItem.deadline_at && (
                  <p>
                    <strong>Deadline Time:</strong>{' '}
                    {formatDateTimeWithTz(selectedItem.deadline_at)}
                  </p>
                )}
                {selectedItem.snoozed_until && (
                  <p>
                    <strong>Snoozed Until:</strong>{' '}
                    {formatDateTimeWithTz(selectedItem.snoozed_until)}
                  </p>
                )}
                {selectedItem.acknowledged_at && (
                  <p>
                    <strong>Acknowledged At:</strong>{' '}
                    {formatDateTimeWithTz(selectedItem.acknowledged_at)}
                  </p>
                )}
                {selectedItem.completed_at && (
                  <p>
                    <strong>Completed At:</strong>{' '}
                    {formatDateTimeWithTz(selectedItem.completed_at)}
                  </p>
                )}
              </div>

              {selectedItem.summary && (
                <div className="detail-section">
                  <h4>Summary &amp; Instructions</h4>
                  <p className="summary-text">{selectedItem.summary}</p>
                </div>
              )}

              {selectedItem.links && selectedItem.links.length > 0 && (
                <div className="detail-section">
                  <h4>Associated Links</h4>
                  <ul className="links-list">
                    {selectedItem.links.map((link, idx) => (
                      <li key={idx}>
                        <a href={link} target="_blank" rel="noopener noreferrer">
                          {link}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Source Evidence (Plain Text Only) */}
              <div className="detail-section evidence-section">
                <h4>Source Evidence</h4>
                <div className="evidence-meta">
                  <span>
                    <strong>Source:</strong> {selectedItem.source_type.toUpperCase()}{' '}
                    {selectedItem.source_title ? `(${selectedItem.source_title})` : ''}
                    {selectedItem.is_demo ? ' [DEMO]' : ''}
                  </span>
                  {selectedItem.source_type === 'gmail' && selectedItem.source_id && (
                    <span>
                      <a
                        href={`https://mail.google.com/mail/u/0/#inbox/${selectedItem.source_id}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="gmail-source-link"
                      >
                        ✉️ Open in Gmail ↗
                      </a>
                    </span>
                  )}
                  {selectedItem.sender && (
                    <span>
                      <strong>Sender:</strong> {selectedItem.sender}
                    </span>
                  )}
                  {selectedItem.received_at && (
                    <span>
                      <strong>Received:</strong>{' '}
                      {formatDateTimeWithTz(selectedItem.received_at)}
                    </span>
                  )}
                </div>
                <pre className="evidence-box">
                  {selectedItem.evidence || 'No source evidence recorded.'}
                </pre>
              </div>
            </div>
          </aside>
        );})()}
      </div>

      {/* Edit Form Modal */}
      {editingItem && (
        <div className="modal-overlay" role="dialog" aria-modal="true">
          <div className="modal-card">
            <div className="modal-header">
              <h3>Edit Item #{editingItem.id}</h3>
              <button className="btn-icon" onClick={() => setEditingItem(null)}>
                ✕
              </button>
            </div>

            <form onSubmit={handleSaveEdit} className="form">
              <div className="form-group">
                <label htmlFor="edit-title">Title *</label>
                <input
                  id="edit-title"
                  type="text"
                  required
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                />
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="edit-kind">Kind</label>
                  <select
                    id="edit-kind"
                    value={editKind}
                    onChange={(e) => setEditKind(e.target.value as ItemKind)}
                  >
                    <option value="assignment">Assignment</option>
                    <option value="announcement">Announcement</option>
                    <option value="recruiter">Recruiter</option>
                    <option value="interview">Interview</option>
                    <option value="document_request">Document Request</option>
                    <option value="event">Event</option>
                    <option value="meeting">Meeting</option>
                    <option value="routine">Routine</option>
                    <option value="other">Other</option>
                  </select>
                </div>

                <div className="form-group">
                  <label htmlFor="edit-type">Item Type</label>
                  <select
                    id="edit-type"
                    value={editItemType}
                    onChange={(e) => setEditItemType(e.target.value as ItemType)}
                  >
                    <option value="task">Task</option>
                    <option value="information">Information</option>
                  </select>
                </div>
              </div>

              <div className="form-group">
                <label htmlFor="edit-deadline-text">Deadline Text</label>
                <input
                  id="edit-deadline-text"
                  type="text"
                  placeholder="e.g. Next Friday 5 PM"
                  value={editDeadlineText}
                  onChange={(e) => setEditDeadlineText(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label htmlFor="edit-deadline-at">
                  Deadline Date &amp; Time
                  <span className="field-note">
                    (Shown in your browser time zone: <strong>{localTimezone}</strong>; saved as UTC)
                  </span>
                </label>
                <input
                  id="edit-deadline-at"
                  type="datetime-local"
                  value={editDeadlineAt}
                  onChange={(e) => setEditDeadlineAt(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label htmlFor="edit-priority-override">
                  Priority Override
                  <span className="field-note">(Override set by you)</span>
                </label>
                <select
                  id="edit-priority-override"
                  value={editPriorityOverride}
                  onChange={(e) => setEditPriorityOverride(e.target.value)}
                >
                  <option value="none">None (Use system priority: {editingItem.priority})</option>
                  <option value="normal">Normal</option>
                  <option value="important">Important</option>
                  <option value="urgent">Urgent</option>
                </select>
              </div>

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setEditingItem(null)}
                >
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary">
                  Save Changes
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Manual Paste Modal */}
      {showPasteModal && (
        <div className="modal-overlay" role="dialog" aria-modal="true">
          <div className="modal-card">
            <div className="modal-header">
              <h3>Paste Email or Announcement</h3>
              <button className="btn-icon" onClick={() => setShowPasteModal(false)}>
                ✕
              </button>
            </div>

            <form onSubmit={handleManualSubmit} className="form">
              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="paste-source">Source Title / Course (optional)</label>
                  <input
                    id="paste-source"
                    type="text"
                    placeholder="e.g. CS 101 or Initech Careers"
                    value={pasteSourceTitle}
                    onChange={(e) => setPasteSourceTitle(e.target.value)}
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="paste-sender">Sender Email / Name (optional)</label>
                  <input
                    id="paste-sender"
                    type="text"
                    placeholder="e.g. professor@college.edu"
                    value={pasteSender}
                    onChange={(e) => setPasteSender(e.target.value)}
                  />
                </div>
              </div>

              <div className="form-group">
                <label htmlFor="paste-evidence">Pasted Email / Announcement Text *</label>
                <textarea
                  id="paste-evidence"
                  rows={8}
                  required
                  placeholder="Paste the raw email, notification, or classroom announcement text here..."
                  value={pasteEvidence}
                  onChange={(e) => setPasteEvidence(e.target.value)}
                ></textarea>
              </div>

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowPasteModal(false)}
                >
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={pasteSubmitting}>
                  {pasteSubmitting ? 'Analyzing & Ingesting...' : 'Analyze & Ingest'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
