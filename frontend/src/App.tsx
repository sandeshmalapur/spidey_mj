import React, { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import type {
  AuthMeResponse,
  ConnectionsResponse,
  HealthResponse,
  Item,
  ItemKind,
  ItemState,
  ItemType,
  ItemUpdateParams,
  NotificationLogEntry,
  NotificationStatus,
  Priority,
  Reminder,
  SyncStatus,
  SystemStatusResponse,
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
    assessment: 'Assessment',
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

function getAssessmentStartBy(item: Item): string | null {
  if (item.kind !== 'assessment') return null;
  if (!item.duration_minutes) {
    return 'Add duration to get a start-time reminder';
  }
  if (!item.deadline_at) return null;
  const d = new Date(item.deadline_at);
  if (isNaN(d.getTime())) return null;
  const startBy = new Date(d.getTime() - item.duration_minutes * 60000);
  const timeStr = startBy.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return `Start by ${timeStr}`;
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
  const [editDurationMinutes, setEditDurationMinutes] = useState<string>('');
  const [editPriorityOverride, setEditPriorityOverride] = useState<string>('none');
  const [editKind, setEditKind] = useState<ItemKind>('other');
  const [editItemType, setEditItemType] = useState<ItemType>('task');
  const [itemReminders, setItemReminders] = useState<Reminder[]>([]);

  // Manual paste state
  const [pasteSourceTitle, setPasteSourceTitle] = useState('');
  const [pasteSender, setPasteSender] = useState('');
  const [pasteEvidence, setPasteEvidence] = useState('');
  const [pasteSubmitting, setPasteSubmitting] = useState(false);

  // Sync state
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [syncing, setSyncing] = useState<boolean>(false);
  const [syncingClassroom, setSyncingClassroom] = useState<boolean>(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [togglingSync, setTogglingSync] = useState<boolean>(false);

  // Re-analyze state
  const [reanalyzingAll, setReanalyzingAll] = useState<boolean>(false);
  const [reanalyzingItem, setReanalyzingItem] = useState<boolean>(false);

  // Reminders & Notifications state
  const [showNotificationsModal, setShowNotificationsModal] = useState<boolean>(false);
  const [notificationLogs, setNotificationLogs] = useState<NotificationLogEntry[]>([]);
  const [notifStatus, setNotifStatus] = useState<NotificationStatus | null>(null);
  const [testingNotification, setTestingNotification] = useState<boolean>(false);
  const [togglingNotif, setTogglingNotif] = useState<boolean>(false);
  const [testNotifResult, setTestNotifResult] = useState<{
    status: string;
    channel: string;
    outcome: string;
    error?: string | null;
  } | null>(null);

  // Authentication & Connections state
  const [authMe, setAuthMe] = useState<AuthMeResponse | null>(null);
  const [connections, setConnections] = useState<ConnectionsResponse | null>(null);
  const [checkingAuth, setCheckingAuth] = useState<boolean>(true);

  const loadConnections = async () => {
    try {
      const conns = await api.getConnections();
      setConnections(conns);
    } catch {
      // Ignore background load error
    }
  };

  const handleDisconnect = async (source: 'gmail' | 'classroom') => {
    const label = source === 'gmail' ? 'Gmail' : 'Google Classroom';
    if (!window.confirm(`Are you sure you want to disconnect ${label}? Stored credentials will be removed.`)) {
      return;
    }
    try {
      await api.disconnect(source);
      setActionSuccess(`Disconnected ${label}.`);
      setTimeout(() => setActionSuccess(null), 4000);
      await Promise.all([loadConnections(), loadSyncStatus(), loadItems()]);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : `Failed to disconnect ${label}`);
    }
  };

  const handleLogout = async () => {
    try {
      await api.logout();
      setAuthMe({ authenticated: false, auth_enabled: true, email: null, display_name: null });
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Logout failed');
    }
  };

  // System status state
  const [showSystemStatusModal, setShowSystemStatusModal] = useState<boolean>(false);
  const [systemStatus, setSystemStatus] = useState<SystemStatusResponse | null>(null);
  const [loadingSystemStatus, setLoadingSystemStatus] = useState<boolean>(false);

  const loadSystemStatus = async () => {
    setLoadingSystemStatus(true);
    try {
      const st = await api.getSystemStatus();
      setSystemStatus(st);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to fetch system status');
    } finally {
      setLoadingSystemStatus(false);
    }
  };

  // Load notification logs and status
  const loadNotificationInfo = async () => {
    try {
      const [logs, st] = await Promise.all([
        api.getNotifications(30),
        api.getNotificationStatus(),
      ]);
      setNotificationLogs(logs);
      setNotifStatus(st);
    } catch {
      // Ignore background load error
    }
  };

  const handleToggleNotifications = async () => {
    if (!notifStatus) return;
    setTogglingNotif(true);
    try {
      const updated = await api.updateNotificationSettings(!notifStatus.notifications_enabled);
      setNotifStatus(updated);
      setActionSuccess(updated.notifications_enabled ? 'Notifications enabled' : 'Notifications paused');
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to update notification settings');
    } finally {
      setTogglingNotif(false);
    }
  };

  const handleSendTestNotification = async () => {
    setTestingNotification(true);
    setTestNotifResult(null);
    try {
      const res = await api.sendTestNotification();
      setTestNotifResult(res);
      await loadNotificationInfo();
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Test notification failed');
    } finally {
      setTestingNotification(false);
    }
  };

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

  useEffect(() => {
    if (selectedItem?.id) {
      api.getItemReminders(selectedItem.id)
        .then(setItemReminders)
        .catch(() => setItemReminders([]));
    } else {
      setItemReminders([]);
    }
  }, [selectedItem?.id]);

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

  // Trigger Google Classroom Sync
  const handleSyncClassroom = async () => {
    setSyncingClassroom(true);
    setSyncError(null);
    try {
      const res = await api.syncClassroom();
      if (res.status === 'error' || res.status === 'reconnect_needed') {
        setSyncError(res.error_message || res.message);
      } else {
        setActionSuccess(res.message);
        setTimeout(() => setActionSuccess(null), 5000);
      }
      await loadSyncStatus();
      await loadItems();
    } catch (err: unknown) {
      setSyncError(err instanceof Error ? err.message : 'Classroom sync failed');
    } finally {
      setSyncingClassroom(false);
    }
  };

  // Toggle Auto-sync Runtime Setting
  const handleToggleAutoSync = async () => {
    if (!syncStatus) return;
    setTogglingSync(true);
    try {
      setErrorMessage(null);
      const nextVal = !syncStatus.auto_sync_enabled;
      const updated = await api.updateSyncSettings(nextVal);
      setSyncStatus(updated);
      setActionSuccess(nextVal ? 'Auto-sync resumed' : 'Auto-sync paused');
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to update auto-sync settings');
    } finally {
      setTogglingSync(false);
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

  const checkAuthAndLoad = async () => {
    try {
      const me = await api.getAuthMe();
      setAuthMe(me);
      if (!me.auth_enabled || me.authenticated) {
        checkHealth();
        loadItems();
        loadSyncStatus();
        loadNotificationInfo();
        loadConnections();
      }
    } catch {
      setAuthMe({ authenticated: false, auth_enabled: true, email: null, display_name: null });
    } finally {
      setCheckingAuth(false);
    }
  };

  useEffect(() => {
    checkAuthAndLoad();

    const interval = setInterval(() => {
      if (!authMe?.auth_enabled || authMe?.authenticated) {
        loadSyncStatus();
        loadNotificationInfo();
        loadConnections();
      }
    }, 10000);
    return () => clearInterval(interval);
  }, [authMe?.authenticated, authMe?.auth_enabled]);

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

  const handleResetDemo = async () => {
    try {
      setErrorMessage(null);
      const res = await api.resetDemo();
      setActionSuccess(`Reset demo items (${res.seeded} seeded, total demo: ${res.total_demo})`);
      setTimeout(() => setActionSuccess(null), 3000);
      await loadItems();
      if (selectedItem?.is_demo) setSelectedItem(null);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to reset demo data');
    }
  };

  // Re-analyze handlers
  const handleReanalyzeAll = async () => {
    setReanalyzingAll(true);
    try {
      setErrorMessage(null);
      const res = await api.reanalyzeAll();
      setActionSuccess(res.message);
      setTimeout(() => setActionSuccess(null), 4000);
      await loadItems();
      if (selectedItem) {
        // Refresh selected item from list if updated
        const refreshed = await api.getItem(selectedItem.id);
        setSelectedItem(refreshed);
      }
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to re-analyze items');
    } finally {
      setReanalyzingAll(false);
    }
  };

  const handleReanalyzeItem = async (itemId: number) => {
    setReanalyzingItem(true);
    try {
      setErrorMessage(null);
      const updatedItem = await api.reanalyzeItem(itemId);
      setActionSuccess(`Re-analyzed item #${updatedItem.id}`);
      setTimeout(() => setActionSuccess(null), 3500);
      setSelectedItem(updatedItem);
      await loadItems();
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to re-analyze item');
    } finally {
      setReanalyzingItem(false);
    }
  };

  // Open Edit Form
  const openEdit = (item: Item) => {
    setEditingItem(item);
    setEditTitle(item.title);
    setEditDeadlineText(item.deadline_text || '');
    setEditDurationMinutes(item.duration_minutes ? String(item.duration_minutes) : '');
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

    let durationVal: number | null = null;
    if (editDurationMinutes.trim()) {
      const parsedDur = parseInt(editDurationMinutes.trim(), 10);
      if (!isNaN(parsedDur) && parsedDur > 0) {
        durationVal = parsedDur;
      }
    }

    const payload: ItemUpdateParams = {
      title: editTitle.trim(),
      deadline_text: editDeadlineText.trim() || null,
      deadline_at: deadlineAtIso,
      duration_minutes: durationVal,
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

  if (checkingAuth) {
    return (
      <div className="login-container">
        <div style={{ textAlign: 'center', color: '#64748b' }}>
          <p>Loading ClassMail J...</p>
        </div>
      </div>
    );
  }

  if (authMe?.auth_enabled && !authMe?.authenticated) {
    return (
      <div className="login-container">
        <div className="login-card">
          <div className="login-logo">
            <span className="logo-icon">📬</span>
            <h1>ClassMail J</h1>
          </div>
          <p className="login-subtitle">Local AI Email &amp; Classroom Assistant</p>
          <div className="login-divider" />
          <p className="login-desc">
            Sign in with your Google Account to authorize ClassMail J to access your Gmail and Google Classroom read-only.
          </p>
          <a href="http://127.0.0.1:8000/api/auth/google/start" className="google-signin-btn">
            <svg className="google-icon" viewBox="0 0 24 24" width="20" height="20">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z" />
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" />
            </svg>
            <span>Sign in with Google</span>
          </a>
          <p className="login-note">
            This is a single-owner instance. Only the designated instance owner account is permitted.
          </p>
        </div>
      </div>
    );
  }

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

          {/* Background Auto-sync Status & Toggle */}
          {syncStatus && (
            <>
              {syncStatus.status === 'reconnect_needed' ? (
                <div className="health-pill warning" title="Gmail authorization expired or missing">
                  <span className="dot dot-warning"></span>
                  <span>⚠️ Reconnect needed</span>
                </div>
              ) : syncStatus.auto_sync_enabled ? (
                <div
                  className="health-pill success"
                  title={
                    syncStatus.next_run_at
                      ? `Next sync: ${formatDateTimeWithTz(syncStatus.next_run_at)}`
                      : 'Auto-sync active'
                  }
                >
                  <span className="dot dot-success"></span>
                  <span>Auto-sync every {syncStatus.interval_minutes} min</span>
                </div>
              ) : (
                <div className="health-pill neutral" title="Background auto-sync is currently paused">
                  <span className="dot dot-neutral"></span>
                  <span>Auto-sync paused</span>
                </div>
              )}
              <button
                className="btn btn-secondary btn-sm"
                onClick={handleToggleAutoSync}
                disabled={togglingSync}
                title={
                  syncStatus.auto_sync_enabled
                    ? 'Pause background synchronization'
                    : 'Resume background synchronization'
                }
              >
                {togglingSync
                  ? 'Updating...'
                  : syncStatus.auto_sync_enabled
                  ? 'Pause Auto-sync'
                  : 'Enable Auto-sync'}
              </button>
            </>
          )}

          <button
            className="btn btn-secondary btn-sm"
            onClick={() => {
              setShowSystemStatusModal(true);
              loadSystemStatus();
            }}
          >
            System Status
          </button>
          <button className="btn btn-secondary btn-sm" onClick={checkHealth}>
            Check Health
          </button>

          {authMe?.auth_enabled && authMe?.authenticated && (
            <div className="auth-user-info">
              <span className="user-email-tag" title="Instance Owner">👤 {authMe.email}</span>
              <button onClick={handleLogout} className="btn btn-secondary btn-sm">
                Sign Out
              </button>
            </div>
          )}
        </div>
      </header>

      {/* Demo Banner */}
      {hasDemoItems && (
        <div className="demo-banner">
          <div className="demo-banner-content">
            <span className="demo-banner-icon">⚠️</span>
            <span>
              <strong>Demo data active:</strong> Simulated academic items are loaded alongside real data.
            </span>
          </div>
          <div className="demo-banner-actions">
            <button className="btn btn-sm btn-outline" onClick={handleResetDemo}>
              Reset Demo
            </button>
            <button className="btn btn-sm btn-outline btn-danger" onClick={handleDeleteDemo}>
              Clear Demo
            </button>
          </div>
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

      {/* Integrations Connection Strip */}
      <div className="connections-strip">
        <div className="connection-pill-group">
          <span className="connection-group-title">Integrations:</span>

          {/* Gmail badge */}
          <div
            className={`connection-badge ${
              connections?.gmail?.needs_reconnect || syncStatus?.status === 'reconnect_needed'
                ? 'badge-warning'
                : connections?.gmail?.connected
                ? 'badge-success'
                : 'badge-muted'
            }`}
          >
            {connections?.gmail?.needs_reconnect || syncStatus?.status === 'reconnect_needed' ? (
              <>
                <span>⚠️ Reconnect needed (Gmail)</span>
                <a href="http://127.0.0.1:8000/api/auth/google/start" className="btn-link">
                  Reconnect
                </a>
              </>
            ) : connections?.gmail?.connected ? (
              <>
                <span>✓ Gmail connected</span>
                <button
                  onClick={() => handleDisconnect('gmail')}
                  className="btn-link text-danger"
                  title="Disconnect Gmail integration and revoke token"
                >
                  Disconnect
                </button>
              </>
            ) : (
              <>
                <span>○ Gmail not granted</span>
                <a href="http://127.0.0.1:8000/api/auth/google/start" className="btn-link">
                  Connect
                </a>
              </>
            )}
          </div>

          {/* Classroom badge */}
          <div
            className={`connection-badge ${
              connections?.classroom?.needs_reconnect || syncStatus?.classroom_status === 'reconnect_needed'
                ? 'badge-warning'
                : connections?.classroom?.connected
                ? 'badge-success'
                : 'badge-muted'
            }`}
          >
            {connections?.classroom?.needs_reconnect || syncStatus?.classroom_status === 'reconnect_needed' ? (
              <>
                <span>⚠️ Reconnect needed (Classroom)</span>
                <a href="http://127.0.0.1:8000/api/auth/google/start" className="btn-link">
                  Reconnect
                </a>
              </>
            ) : connections?.classroom?.connected ? (
              <>
                <span>✓ Classroom connected</span>
                <button
                  onClick={() => handleDisconnect('classroom')}
                  className="btn-link text-danger"
                  title="Disconnect Google Classroom integration and revoke token"
                >
                  Disconnect
                </button>
              </>
            ) : (
              <>
                <span>○ Classroom not granted</span>
                <a href="http://127.0.0.1:8000/api/auth/google/start" className="btn-link">
                  Connect
                </a>
              </>
            )}
          </div>
        </div>
      </div>

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
          <button
            className="btn btn-classroom"
            onClick={handleSyncClassroom}
            disabled={syncingClassroom}
            title="Fetch active coursework and announcements from Google Classroom"
          >
            {syncingClassroom ? '⏳ Syncing Classroom...' : '📥 Sync Classroom now'}
          </button>
          <button
            className="btn btn-outline"
            onClick={handleReanalyzeAll}
            disabled={reanalyzingAll}
            title="Re-run extraction rules on all active items"
          >
            {reanalyzingAll ? '⏳ Re-analyzing...' : '🔄 Re-analyze all'}
          </button>
          <button
            className="btn btn-outline"
            onClick={() => {
              setShowNotificationsModal(true);
              loadNotificationInfo();
            }}
            title="View notification settings and delivery logs"
          >
            🔔 Notifications {notifStatus && !notifStatus.notifications_enabled ? '(Off)' : ''}
          </button>
        </div>
        <div className="toolbar-right">
          <button className="btn btn-outline" onClick={handleResetDemo} title="Clear and reload fresh relative-date demo items">
            Reset Demo Data
          </button>
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

      {/* Gmail Reconnect Needed Banner */}
      {syncStatus?.status === 'reconnect_needed' && (
        <div className="reconnect-banner">
          <div className="reconnect-header">
            <span className="reconnect-icon">⚠️</span>
            <strong>Gmail Reconnect Needed:</strong> Authorization token is missing, expired, or revoked.
          </div>
          <div className="reconnect-body">
            <span>Automatic background sync for Gmail is paused. Click below to re-authorize via browser, or use the CLI command:</span>
            <div style={{ marginTop: '0.6rem', display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
              <a href="http://127.0.0.1:8000/api/auth/google/start" className="btn btn-primary btn-sm">
                Reconnect in Browser
              </a>
              <span style={{ fontSize: '0.85rem', color: '#92400e' }}>or run terminal command:</span>
              <code className="reconnect-code">{syncStatus.reconnect_command || 'python -m app.integrations.google_auth gmail'}</code>
            </div>
          </div>
        </div>
      )}

      {/* Classroom Reconnect Needed Banner */}
      {syncStatus?.classroom_status === 'reconnect_needed' && (
        <div className="reconnect-banner">
          <div className="reconnect-header">
            <span className="reconnect-icon">⚠️</span>
            <strong>Google Classroom Reconnect Needed:</strong> Authorization token is missing, expired, or revoked.
          </div>
          <div className="reconnect-body">
            <span>Click below to authorize Google Classroom via browser, or use the CLI command:</span>
            <div style={{ marginTop: '0.6rem', display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
              <a href="http://127.0.0.1:8000/api/auth/google/start" className="btn btn-primary btn-sm">
                Reconnect in Browser
              </a>
              <span style={{ fontSize: '0.85rem', color: '#92400e' }}>or run terminal command:</span>
              <code className="reconnect-code">{syncStatus.classroom_reconnect_command || 'python -m app.integrations.google_auth classroom'}</code>
            </div>
          </div>
        </div>
      )}

      {syncError && syncStatus?.status !== 'reconnect_needed' && syncStatus?.classroom_status !== 'reconnect_needed' && (
        <div className="sync-error-banner">
          <span className="sync-error-icon">⚠️</span>
          <div className="sync-error-content">
            <strong>Sync Note:</strong> {syncError}
          </div>
          <button className="alert-close" onClick={() => setSyncError(null)}>
            ×
          </button>
        </div>
      )}

      {syncStatus && (syncStatus.last_synced_at || syncStatus.next_run_at) && (
        <div className="sync-info-bar">
          <div>
            <strong>Gmail Sync:</strong>{' '}
            {syncStatus.last_synced_at
              ? `Last run at ${formatDateTimeWithTz(syncStatus.last_synced_at)}`
              : 'Never run'}
            {syncStatus.next_run_at && syncStatus.auto_sync_enabled && syncStatus.status !== 'reconnect_needed' ? (
              <span className="next-sync-label">
                {' '}
                | Next run at {formatDateTimeWithTz(syncStatus.next_run_at)}
              </span>
            ) : null}
            {syncStatus.status === 'reconnect_needed' ? (
              <span className="badge badge-warning" style={{ marginLeft: '8px' }}>
                Reconnect needed
              </span>
            ) : null}
          </div>
          <span>
            Fetched: {syncStatus.counts.fetched} | Created: {syncStatus.counts.created} | Updated:{' '}
            {syncStatus.counts.updated} | Skipped: {syncStatus.counts.skipped}
            {syncStatus.counts.errors > 0 ? ` | Errors: ${syncStatus.counts.errors}` : ''}
          </span>
        </div>
      )}

      {syncStatus && syncStatus.classroom_last_synced_at && (
        <div className="sync-info-bar" style={{ marginTop: '0.4rem' }}>
          <div>
            <strong>Classroom Sync:</strong> Last run at {formatDateTimeWithTz(syncStatus.classroom_last_synced_at)}
            {syncStatus.classroom_status === 'reconnect_needed' ? (
              <span className="badge badge-warning" style={{ marginLeft: '8px' }}>
                Reconnect needed
              </span>
            ) : null}
          </div>
          <span>
            Courses: {syncStatus.classroom_counts?.courses || 0} | Created: {syncStatus.classroom_counts?.created || 0} | Updated:{' '}
            {syncStatus.classroom_counts?.updated || 0} | Skipped: {syncStatus.classroom_counts?.skipped || 0}
            {syncStatus.classroom_counts && syncStatus.classroom_counts.errors > 0 ? ` | Errors: ${syncStatus.classroom_counts.errors}` : ''}
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
                      {item.source_type === 'classroom' && (
                        <span className="badge badge-classroom" title="Imported from Google Classroom">
                          🏫 Classroom
                        </span>
                      )}
                      {item.source_type === 'manual' && (
                        <span className="badge badge-manual" title="Entered manually">
                          📝 Manual
                        </span>
                      )}
                      {item.source_status === 'TURNED_IN' && (
                        <span className="badge badge-turned-in" title="Turned in per Google Classroom">
                          ✓ Turned in per Classroom
                        </span>
                      )}
                      {item.is_demo && <span className="badge badge-demo">DEMO</span>}
                      {item.duration_minutes && (
                        <span className="badge badge-duration">Duration {item.duration_minutes} min</span>
                      )}
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
                      {item.kind === 'assessment' && (
                        <span className={`badge ${item.duration_minutes ? 'badge-start-by' : 'badge-no-duration'}`}>
                          ⏱️ {getAssessmentStartBy(item)}
                        </span>
                      )}
                      {item.next_reminder_at && item.state !== 'completed' && item.state !== 'cancelled' && (
                        <span className="card-next-reminder" title={`Next reminder scheduled for ${item.next_reminder_at}`}>
                          ⏰ Next: {new Date(item.next_reminder_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                        </span>
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
                    {selectedItem.source_type === 'gmail' && (
                      <span className="badge badge-gmail" title="Imported from Gmail">
                        ✉️ Gmail
                      </span>
                    )}
                    {selectedItem.source_type === 'classroom' && (
                      <span className="badge badge-classroom" title="Imported from Google Classroom">
                        🏫 Classroom
                      </span>
                    )}
                    {selectedItem.source_type === 'manual' && (
                      <span className="badge badge-manual" title="Entered manually">
                        📝 Manual
                      </span>
                    )}
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
                    onClick={() => handleReanalyzeItem(selectedItem.id)}
                    disabled={reanalyzingItem}
                    title="Re-run extraction on evidence"
                  >
                    {reanalyzingItem ? '⏳ Re-analyzing...' : '🔄 Re-analyze'}
                  </button>

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
                {selectedItem.duration_minutes && (
                  <p>
                    <strong>Duration:</strong> {selectedItem.duration_minutes} min
                  </p>
                )}
                {selectedItem.kind === 'assessment' && (
                  <p>
                    <strong>Assessment Start:</strong>{' '}
                    <span className="badge badge-start-by">⏱️ {getAssessmentStartBy(selectedItem)}</span>
                  </p>
                )}
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
                <p>
                  <strong>Next Reminder:</strong>{' '}
                  {selectedItem.next_reminder_at && selectedItem.state !== 'completed' && selectedItem.state !== 'cancelled' ? (
                    <span className="reminder-highlight">
                      ⏰ {formatDateTimeWithTz(selectedItem.next_reminder_at)} ({selectedItem.next_reminder_kind})
                    </span>
                  ) : selectedItem.state === 'completed' || selectedItem.state === 'cancelled' ? (
                    <span className="text-muted">None (item closed)</span>
                  ) : (
                    <span className="text-muted">No reminder scheduled</span>
                  )}
                </p>
              </div>

              <div className="detail-section detail-reminders-section">
                <h4>Next reminders</h4>
                {itemReminders.filter((r) => r.status === 'pending').length > 0 ? (
                  <ul className="reminders-list">
                    {itemReminders
                      .filter((r) => r.status === 'pending')
                      .map((r) => (
                        <li key={r.id}>
                          <strong>{r.kind.replace(/_/g, ' ')}:</strong> {formatDateTimeWithTz(r.scheduled_for)}
                          {r.reason && <span className="text-muted"> — {r.reason}</span>}
                        </li>
                      ))}
                  </ul>
                ) : (
                  <p className="text-muted">No pending reminders</p>
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
                  {selectedItem.source_type === 'classroom' && selectedItem.links && selectedItem.links.length > 0 && (
                    <span>
                      <a
                        href={selectedItem.links[0]}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="source-link"
                      >
                        🏫 Open in Classroom ↗
                      </a>
                    </span>
                  )}
                  {selectedItem.source_status && (
                    <span>
                      <strong>Classroom Status:</strong>{' '}
                      <span className={`badge ${selectedItem.source_status === 'TURNED_IN' ? 'badge-turned-in' : 'badge-state'}`}>
                        {selectedItem.source_status === 'TURNED_IN' ? '✓ Turned in per Classroom' : selectedItem.source_status}
                      </span>
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
                    <option value="assessment">Assessment</option>
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
                <label htmlFor="edit-duration">
                  Duration (minutes)
                  <span className="field-note">(For assessments; 5 to 600 min)</span>
                </label>
                <input
                  id="edit-duration"
                  type="number"
                  min="5"
                  max="600"
                  placeholder="e.g. 45"
                  value={editDurationMinutes}
                  onChange={(e) => setEditDurationMinutes(e.target.value)}
                />
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

      {/* Notifications Panel Modal */}
      {showNotificationsModal && (
        <div className="modal-overlay" role="dialog" aria-modal="true">
          <div className="modal-card modal-large">
            <div className="modal-header">
              <h3>🔔 Reminders &amp; Notification Center</h3>
              <button className="btn-icon" onClick={() => setShowNotificationsModal(false)}>
                ✕
              </button>
            </div>

            <div className="notif-modal-content">
              {/* Status Header Cards */}
              <div className="notif-status-grid">
                <div className="notif-card">
                  <span className="notif-card-label">System Status</span>
                  <div className="notif-card-value">
                    {notifStatus?.notifications_enabled ? (
                      <span className="badge badge-success">✓ Active</span>
                    ) : (
                      <span className="badge badge-neutral">Paused</span>
                    )}
                  </div>
                  <button
                    className="btn btn-outline btn-xs"
                    onClick={handleToggleNotifications}
                    disabled={togglingNotif}
                  >
                    {togglingNotif ? 'Updating...' : notifStatus?.notifications_enabled ? 'Pause Notifications' : 'Enable Notifications'}
                  </button>
                </div>

                <div className="notif-card">
                  <span className="notif-card-label">ntfy Channel</span>
                  <div className="notif-card-value">
                    {notifStatus?.ntfy_configured ? (
                      <span className="badge badge-ntfy">✓ Configured</span>
                    ) : (
                      <span className="badge badge-warning">Local only</span>
                    )}
                  </div>
                  <span className="notif-card-hint">
                    {notifStatus?.ntfy_configured ? 'Pushing to ntfy server' : 'Set NTFY_TOPIC in .env to enable'}
                  </span>
                </div>

                <div className="notif-card">
                  <span className="notif-card-label">Quiet Hours</span>
                  <div className="notif-card-value">
                    <strong>{notifStatus?.quiet_hours_start || '22:00'} - {notifStatus?.quiet_hours_end || '07:00'}</strong>
                  </div>
                  <span className="notif-card-hint">
                    {notifStatus?.urgent_bypass_quiet ? 'Urgent alerts bypass' : 'Deferred until morning'}
                  </span>
                </div>

                <div className="notif-card">
                  <span className="notif-card-label">Frequency Cap</span>
                  <div className="notif-card-value">
                    <strong>Max {notifStatus?.max_per_hour || 5} / hour</strong>
                  </div>
                  <span className="notif-card-hint">
                    {notifStatus?.recent_count || 0} sent in last hour
                  </span>
                </div>
              </div>

              {/* Action Banner */}
              <div className="notif-test-bar">
                <div>
                  <strong>Test Notification Delivery</strong>
                  <p className="notif-subtext">Sends a minimal test message and records it in the delivery log.</p>
                </div>
                <button
                  className="btn btn-primary"
                  onClick={handleSendTestNotification}
                  disabled={testingNotification}
                >
                  {testingNotification ? '⏳ Sending Test...' : '🔔 Send Test Alert'}
                </button>
              </div>

              {testNotifResult && (
                <div className={`alert ${testNotifResult.status === 'delivered' ? 'alert-success' : 'alert-warning'}`}>
                  <span>
                    Test notification {testNotifResult.outcome} via <strong>{testNotifResult.channel.toUpperCase()}</strong> channel.
                    {testNotifResult.error && ` (${testNotifResult.error})`}
                  </span>
                  <button className="alert-close" onClick={() => setTestNotifResult(null)}>×</button>
                </div>
              )}

              {/* Logs Table */}
              <div className="notif-logs-section">
                <h4>Recent Notification Logs</h4>
                {notificationLogs.length === 0 ? (
                  <p className="empty-logs">No notification logs recorded yet.</p>
                ) : (
                  <div className="table-responsive">
                    <table className="logs-table">
                      <thead>
                        <tr>
                          <th>Time</th>
                          <th>Scheduled For</th>
                          <th>Delay</th>
                          <th>Channel</th>
                          <th>Title</th>
                          <th>Outcome</th>
                          <th>Error / Details</th>
                        </tr>
                      </thead>
                      <tbody>
                        {notificationLogs.map((log) => (
                          <tr key={log.id}>
                            <td className="log-time">{formatDateTimeWithTz(log.at)}</td>
                            <td className="log-scheduled">{log.scheduled_for ? formatDateTimeWithTz(log.scheduled_for) : '—'}</td>
                            <td className="log-delay">
                              {log.delay_seconds != null ? (
                                log.delay_seconds > 0 ? (
                                  <span className="delay-late">+{log.delay_seconds}s</span>
                                ) : (
                                  <span className="delay-ontime">on-time</span>
                                )
                              ) : (
                                '—'
                              )}
                            </td>
                            <td>
                              <span className={`badge badge-${log.channel}`}>
                                {log.channel.toUpperCase()}
                              </span>
                            </td>
                            <td className="log-title">{log.title}</td>
                            <td>
                              <span className={`badge badge-outcome badge-${log.outcome}`}>
                                {log.outcome}
                              </span>
                            </td>
                            <td className="log-error">{log.error || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowNotificationsModal(false)}
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* System Status Modal */}
      {showSystemStatusModal && (
        <div className="modal-backdrop" onClick={() => setShowSystemStatusModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '600px' }}>
            <div className="modal-header">
              <h2>⚙️ System Diagnostics & Status</h2>
              <button
                type="button"
                className="btn-icon"
                onClick={() => setShowSystemStatusModal(false)}
              >
                ✕
              </button>
            </div>
            <div className="modal-content">
              <p style={{ color: '#64748b', fontSize: '0.875rem', marginTop: 0 }}>
                Diagnostic verification of local services and integrations. No secrets or credentials are ever exposed.
              </p>

              {loadingSystemStatus ? (
                <div style={{ textAlign: 'center', padding: '2rem' }}>Loading system diagnostics...</div>
              ) : systemStatus ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', margin: '1rem 0' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid #e2e8f0' }}>
                    <strong>Database Health:</strong>
                    <span className={systemStatus.database_ok ? 'badge badge-success' : 'badge badge-danger'}>
                      {systemStatus.database_ok ? '✓ SQLite Connected' : '❌ Database Error'}
                    </span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid #e2e8f0' }}>
                    <strong>Gmail OAuth Token:</strong>
                    <span className={systemStatus.gmail_token_present ? 'badge badge-gmail' : 'badge badge-warning'}>
                      {systemStatus.gmail_token_present ? '✓ Authorized' : '⚠️ Reconnect Needed'}
                    </span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid #e2e8f0' }}>
                    <strong>Classroom OAuth Token:</strong>
                    <span className={systemStatus.classroom_token_present ? 'badge badge-classroom' : 'badge badge-warning'}>
                      {systemStatus.classroom_token_present ? '✓ Authorized' : '⚠️ Reconnect Needed'}
                    </span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid #e2e8f0' }}>
                    <strong>Push Notifications (ntfy):</strong>
                    <span className={systemStatus.ntfy_configured ? 'badge badge-ntfy' : 'badge badge-neutral'}>
                      {systemStatus.ntfy_configured ? '✓ Configured' : 'Local Only'}
                    </span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid #e2e8f0' }}>
                    <strong>Scheduler (APScheduler):</strong>
                    <span className={systemStatus.scheduler_running ? 'badge badge-success' : 'badge badge-neutral'}>
                      {systemStatus.scheduler_running ? '✓ Running' : 'Stopped'}
                    </span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid #e2e8f0' }}>
                    <strong>Auto-sync:</strong>
                    <span className={systemStatus.auto_sync_enabled ? 'badge badge-success' : 'badge badge-neutral'}>
                      {systemStatus.auto_sync_enabled ? 'Active' : 'Paused'}
                    </span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid #e2e8f0', fontSize: '0.875rem' }}>
                    <strong>Last Gmail Sync:</strong>
                    <span>{formatDateTimeWithTz(systemStatus.last_sync_gmail) || 'Not synced yet'}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid #e2e8f0', fontSize: '0.875rem' }}>
                    <strong>Last Classroom Sync:</strong>
                    <span>{formatDateTimeWithTz(systemStatus.last_sync_classroom) || 'Not synced yet'}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', fontSize: '0.875rem', color: '#64748b' }}>
                    <strong>ClassMail J Version:</strong>
                    <span>v{systemStatus.version}</span>
                  </div>
                </div>
              ) : (
                <div>Unable to retrieve system diagnostics.</div>
              )}

              <div className="modal-actions" style={{ marginTop: '1.5rem', display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' }}>
                <button
                  type="button"
                  className="btn btn-outline"
                  onClick={loadSystemStatus}
                  disabled={loadingSystemStatus}
                >
                  Refresh
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowSystemStatusModal(false)}
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
