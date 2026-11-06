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

/* ==========================================================================
   Minimalist Enterprise SVG Icons (Clean 1.5-2px stroke, no cartoon emojis)
   ========================================================================== */

const IconSparkle = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/>
  </svg>
);

const IconBell = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>
    <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>
  </svg>
);

const IconSettings = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>
    <circle cx="12" cy="12" r="3"/>
  </svg>
);

const IconMail = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect width="20" height="16" x="2" y="4" rx="2"/>
    <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
  </svg>
);

const IconClassroom = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M22 10v6M2 10l10-5 10 5-10 5z"/>
    <path d="M6 12v5c3 3 9 3 12 0v-5"/>
  </svg>
);

const IconCheck = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12"/>
  </svg>
);

const IconClock = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10"/>
    <polyline points="12 6 12 12 16 14"/>
  </svg>
);

const IconCalendar = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect width="18" height="18" x="3" y="4" rx="2" ry="2"/>
    <line x1="16" x2="16" y1="2" y2="6"/>
    <line x1="8" x2="8" y1="2" y2="6"/>
    <line x1="3" x2="21" y1="10" y2="10"/>
  </svg>
);

const IconAlert = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>
    <line x1="12" y1="9" x2="12" y2="13"/>
    <line x1="12" y1="17" x2="12.01" y2="17"/>
  </svg>
);

const IconShield = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
  </svg>
);

const IconSync = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
    <path d="M3 3v5h5"/>
    <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/>
    <path d="M16 21h5v-5"/>
  </svg>
);

function formatDateTimeWithTz(dateStr: string | null | undefined): string {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return String(dateStr);
  try {
    return d.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZoneName: 'short',
    });
  } catch {
    return d.toISOString();
  }
}

function getRelativeTimeLabel(dateStr: string | null | undefined): { label: string; isPast: boolean } {
  if (!dateStr) return { label: '', isPast: false };
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return { label: String(dateStr), isPast: false };
  const diffMs = d.getTime() - Date.now();
  const diffMins = Math.round(diffMs / 60000);
  const diffHours = Math.round(diffMins / 60);
  const diffDays = Math.round(diffHours / 24);

  if (diffMs < 0) {
    if (Math.abs(diffDays) >= 1) return { label: `${Math.abs(diffDays)}d overdue`, isPast: true };
    if (Math.abs(diffHours) >= 1) return { label: `${Math.abs(diffHours)}h overdue`, isPast: true };
    return { label: `${Math.abs(diffMins)}m overdue`, isPast: true };
  }
  if (diffDays >= 1) return { label: `due in ${diffDays}d`, isPast: false };
  if (diffHours >= 1) return { label: `due in ${diffHours}h`, isPast: false };
  return { label: `due in ${diffMins}m`, isPast: false };
}

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);

  const [items, setItems] = useState<Item[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  // Search and Filters
  const [searchQuery, setSearchQuery] = useState<string>('');
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

  // System diagnostics modal state
  const [showSystemStatusModal, setShowSystemStatusModal] = useState<boolean>(false);
  const [systemStatus, setSystemStatus] = useState<SystemStatusResponse | null>(null);
  const [loadingSystemStatus, setLoadingSystemStatus] = useState<boolean>(false);

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

  const loadItems = async () => {
    setLoading(true);
    setErrorMessage(null);
    try {
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

  const displayedItems = useMemo(() => {
    let result = items.filter((item) => {
      if (!showSnoozed && item.snoozed_until) {
        if (new Date(item.snoozed_until).getTime() > Date.now()) {
          return false;
        }
      }

      if (priorityFilter !== 'all' && item.effective_priority !== priorityFilter) {
        return false;
      }

      if (typeFilter !== 'all' && item.item_type !== typeFilter) {
        return false;
      }

      if (stateFilter !== 'all' && item.state !== stateFilter) {
        return false;
      }

      if (searchQuery.trim()) {
        const query = searchQuery.toLowerCase();
        const matchesTitle = item.title.toLowerCase().includes(query);
        const matchesSummary = item.summary ? item.summary.toLowerCase().includes(query) : false;
        const matchesSender = item.sender ? item.sender.toLowerCase().includes(query) : false;
        if (!matchesTitle && !matchesSummary && !matchesSender) {
          return false;
        }
      }

      return true;
    });

    const priorityWeight: Record<Priority, number> = {
      urgent: 3,
      important: 2,
      normal: 1,
    };

    result.sort((a, b) => {
      const pDiff = priorityWeight[b.effective_priority] - priorityWeight[a.effective_priority];
      if (pDiff !== 0) return pDiff;

      if (a.deadline_at && b.deadline_at) {
        return new Date(a.deadline_at).getTime() - new Date(b.deadline_at).getTime();
      }
      if (a.deadline_at) return -1;
      if (b.deadline_at) return 1;

      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });

    return result;
  }, [items, priorityFilter, typeFilter, stateFilter, showSnoozed, searchQuery]);

  const handleAcknowledge = async (item: Item) => {
    try {
      const updated = await api.acknowledgeItem(item.id);
      setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
      if (selectedItem && selectedItem.id === updated.id) {
        setSelectedItem(updated);
      }
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to acknowledge item');
    }
  };

  const handleStateChange = async (item: Item, newState: ItemState) => {
    try {
      let updated: Item;
      if (newState === 'completed') {
        updated = await api.completeItem(item.id);
      } else if (newState === 'in_progress') {
        updated = await api.startItem(item.id);
      } else if (newState === 'cancelled') {
        updated = await api.cancelItem(item.id);
      } else {
        updated = await api.reopenItem(item.id);
      }
      setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
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
      untilDate = new Date(Date.now() + hours * 60 * 60 * 1000);
    }

    api.snoozeItem(item.id, untilDate.toISOString())
      .then((updated) => {
        setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
        if (selectedItem && selectedItem.id === updated.id) {
          setSelectedItem(updated);
        }
      })
      .catch((err: unknown) => {
        setErrorMessage(err instanceof Error ? err.message : 'Failed to snooze item');
      });
  };

  const handleClearDemo = async () => {
    try {
      setErrorMessage(null);
      const res = await api.deleteDemo();
      setActionSuccess(`Cleared ${res.deleted} demo items.`);
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
      setActionSuccess(`Loaded demo academic items (${res.seeded} seeded).`);
      setTimeout(() => setActionSuccess(null), 3000);
      await loadItems();
      if (selectedItem?.is_demo) setSelectedItem(null);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to reset demo data');
    }
  };

  const handleReanalyzeAll = async () => {
    setReanalyzingAll(true);
    try {
      setErrorMessage(null);
      const res = await api.reanalyzeAll();
      setActionSuccess(res.message);
      setTimeout(() => setActionSuccess(null), 4000);
      await loadItems();
      if (selectedItem) {
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
      setItems((prev) => prev.map((i) => (i.id === updatedItem.id ? updatedItem : i)));
      setSelectedItem(updatedItem);
      setActionSuccess(`Re-analyzed: "${updatedItem.title}"`);
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to re-analyze item');
    } finally {
      setReanalyzingItem(false);
    }
  };

  const handleOpenEdit = (item: Item, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    setEditingItem(item);
    setEditTitle(item.title);
    setEditDeadlineText(item.deadline_text || '');
    setEditDeadlineAt(item.deadline_at ? item.deadline_at.slice(0, 16) : '');
    setEditDurationMinutes(item.duration_minutes ? String(item.duration_minutes) : '');
    setEditPriorityOverride(item.priority_override || 'none');
    setEditKind(item.kind);
    setEditItemType(item.item_type);
  };

  const handleSaveEdit = async () => {
    if (!editingItem) return;
    try {
      const updates: ItemUpdateParams = {
        title: editTitle.trim(),
        deadline_text: editDeadlineText.trim() || null,
        deadline_at: editDeadlineAt ? new Date(editDeadlineAt).toISOString() : null,
        duration_minutes: editDurationMinutes.trim() ? parseInt(editDurationMinutes, 10) : null,
        priority_override: editPriorityOverride === 'none' ? null : (editPriorityOverride as Priority),
        kind: editKind,
        item_type: editItemType,
      };

      const updated = await api.patchItem(editingItem.id, updates);
      setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
      if (selectedItem && selectedItem.id === updated.id) {
        setSelectedItem(updated);
      }
      setEditingItem(null);
      setActionSuccess('Item updated successfully');
      setTimeout(() => setActionSuccess(null), 3000);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to update item');
    }
  };

  const handlePasteSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pasteEvidence.trim()) return;
    setPasteSubmitting(true);
    setErrorMessage(null);
    try {
      const res = await api.ingestManual({
        text: pasteEvidence,
        source_title: pasteSourceTitle.trim() || undefined,
        sender: pasteSender.trim() || undefined,
      });

      if (res.duplicate) {
        setActionSuccess(`Item already exists: "${res.item.title}"`);
      } else {
        setActionSuccess(`Created new item: "${res.item.title}"`);
      }
      setTimeout(() => setActionSuccess(null), 4000);
      setShowPasteModal(false);
      setPasteEvidence('');
      setPasteSourceTitle('');
      setPasteSender('');
      await loadItems();
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to ingest content');
    } finally {
      setPasteSubmitting(false);
    }
  };

  // Sign-in gate if AUTH_ENABLED=true and not authenticated
  if (!checkingAuth && authMe?.auth_enabled && !authMe?.authenticated) {
    return (
      <div className="auth-gate-container">
        <div className="auth-gate-card">
          <div className="auth-gate-logo">
            <span className="brand-icon">J</span>
          </div>
          <h2>Sign in to ClassMail J</h2>
          <p>Local-first academic email & classroom assistant.</p>
          <a href="/api/auth/google/start" className="btn btn-primary btn-lg" style={{ width: '100%' }}>
            Sign in with Google
          </a>
          <p className="auth-gate-note">
            Single-owner authorization mode enabled. Access restricted to configured owner email.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="app-page-wrapper">
      {/* ====================================================================
          Fixed Top Navigation (Minimalist SaaS with clean SVG icons)
          ==================================================================== */}
      <header className="site-nav">
        <div className="site-nav-inner">
          <div className="nav-brand" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
            <div className="brand-icon">J</div>
            <span className="brand-title">ClassMail J</span>
            <span className="brand-badge">Local</span>
          </div>

          <nav className="nav-links">
            <a href="#workspace" className="nav-link">Workspace</a>
            <a href="#features" className="nav-link">Features</a>
            <a href="#integrations" className="nav-link">Integrations</a>
            <a href="#architecture" className="nav-link">Architecture</a>
          </nav>

          <div className="nav-actions">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => {
                loadSystemStatus();
                setShowSystemStatusModal(true);
              }}
              title="System Diagnostics & Health"
            >
              <span className={`status-indicator-dot ${health?.status === 'ok' ? 'dot-online' : 'dot-warning'}`}></span>
              {health?.status === 'ok' ? 'Online' : 'Status'}
            </button>

            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => {
                loadNotificationInfo();
                setShowNotificationsModal(true);
              }}
              title="Reminders & Notifications"
            >
              <IconBell />
              <span>Alerts</span>
            </button>

            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => setShowPasteModal(true)}
            >
              + Ingest Notice
            </button>

            {authMe?.authenticated && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={handleLogout}
                title={`Signed in as ${authMe.email}`}
              >
                Sign out
              </button>
            )}
          </div>
        </div>
      </header>

      {/* ====================================================================
          Hero Section
          ==================================================================== */}
      <section className="hero-section">
        <div className="hero-tagline-pill">
          <span className="status-indicator-dot dot-online"></span>
          <span>Local-First Academic Intelligence • Privacy Preserved</span>
        </div>

        <h1 className="hero-headline">
          Never miss a deadline. <br />
          <span className="gradient-text">Local AI for your academic life.</span>
        </h1>

        <p className="hero-subtext">
          ClassMail J connects your Gmail and Google Classroom to deterministically extract coursework deadlines,
          calculate assessment latest-start countdowns, and schedule push reminders — running 100% locally on your machine with zero cloud leaks.
        </p>

        <div className="hero-cta-group">
          <button
            type="button"
            className="btn btn-primary btn-lg"
            onClick={() => setShowPasteModal(true)}
          >
            + Ingest Notice / Email
          </button>

          <button
            type="button"
            className="btn btn-secondary btn-lg"
            onClick={() => {
              const el = document.getElementById('workspace');
              el?.scrollIntoView({ behavior: 'smooth' });
            }}
          >
            Open Workspace ▾
          </button>

          <button
            type="button"
            className="btn btn-ghost btn-lg"
            onClick={handleResetDemo}
          >
            Load Demo Data
          </button>
        </div>

        <div className="hero-highlights-row">
          <div className="highlight-chip">
            <IconShield />
            <span>100% Local SQLite</span>
          </div>
          <div className="highlight-chip">
            <IconClassroom />
            <span>Classroom Read-Only</span>
          </div>
          <div className="highlight-chip">
            <IconMail />
            <span>Gmail Read-Only</span>
          </div>
          <div className="highlight-chip">
            <IconClock />
            <span>Deterministic Date Math</span>
          </div>
          <div className="highlight-chip">
            <IconSparkle />
            <span>Optional Local LLM</span>
          </div>
          <div className="highlight-chip">
            <IconBell />
            <span>Push Reminders</span>
          </div>
        </div>
      </section>

      {/* ====================================================================
          Continuous Technology Marquee Strip (Clean Minimalist Text)
          ==================================================================== */}
      <div className="marquee-strip">
        <div className="marquee-track">
          <div className="marquee-item"><span className="status-indicator-dot dot-online"></span><strong>FastAPI</strong> Backend</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-online"></span><strong>SQLite stdlib</strong> Storage</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-sync"></span><strong>Classroom API</strong> Read-Only</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-sync"></span><strong>Gmail API</strong> Read-Only</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-online"></span><strong>APScheduler</strong> Background</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-sync"></span><strong>ntfy.sh</strong> Push Alerts</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-sync"></span><strong>Ollama LLM</strong> Optional Inference</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-online"></span><strong>React 19 & TypeScript</strong> Interface</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-online"></span><strong>Zero Cloud Dependencies</strong> Complete Privacy</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-online"></span><strong>FastAPI</strong> Backend</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-online"></span><strong>SQLite stdlib</strong> Storage</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-sync"></span><strong>Classroom API</strong> Read-Only</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-sync"></span><strong>Gmail API</strong> Read-Only</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-online"></span><strong>APScheduler</strong> Background</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-sync"></span><strong>ntfy.sh</strong> Push Alerts</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-sync"></span><strong>Ollama LLM</strong> Optional Inference</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-online"></span><strong>React 19 & TypeScript</strong> Interface</div>
          <div className="marquee-item"><span className="status-indicator-dot dot-online"></span><strong>Zero Cloud Dependencies</strong> Complete Privacy</div>
        </div>
      </div>

      {/* ====================================================================
          Main Interactive Product Showcase / Workspace Container
          ==================================================================== */}
      <section id="workspace" className="workspace-showcase">
        <div className="showcase-container">
          {/* Showcase Top Header */}
          <div className="showcase-header">
            <div className="showcase-headline">
              <h2>Academic Priority Workspace</h2>
              <p>Real-time extracted assignments, assessments, and actionable notices.</p>
            </div>

            <div className="floating-status-strip">
              <div className="status-pill">
                <span className={`status-indicator-dot ${health?.status === 'ok' ? 'dot-online' : 'dot-error'}`}></span>
                <span>{health?.status === 'ok' ? 'Backend Online (ok)' : 'Connecting...'}</span>
              </div>

              <div className="status-pill">
                <span className={`status-indicator-dot ${syncStatus?.auto_sync_enabled ? 'dot-online' : 'dot-warning'}`}></span>
                <span>{syncStatus?.auto_sync_enabled ? 'Auto-sync active (5m)' : 'Auto-sync paused'}</span>
                <button
                  type="button"
                  onClick={handleToggleAutoSync}
                  disabled={togglingSync}
                  style={{ textDecoration: 'underline', cursor: 'pointer', fontSize: '0.78rem', marginLeft: '0.25rem' }}
                >
                  {syncStatus?.auto_sync_enabled ? 'Pause' : 'Resume'}
                </button>
              </div>

              <button
                type="button"
                className="status-pill"
                onClick={() => {
                  loadSystemStatus();
                  setShowSystemStatusModal(true);
                }}
              >
                <IconSettings />
                <span>Diagnostics</span>
              </button>
            </div>
          </div>

          {/* Quick Action Toolbar */}
          <div className="action-toolbar">
            <div className="action-toolbar-left">
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setShowPasteModal(true)}
              >
                + Ingest Email / Text
              </button>

              <button
                type="button"
                className="btn btn-secondary"
                onClick={handleSyncGmail}
                disabled={syncing}
              >
                <IconMail />
                <span>{syncing ? 'Syncing Gmail...' : 'Sync Gmail'}</span>
              </button>

              <button
                type="button"
                className="btn btn-secondary"
                onClick={handleSyncClassroom}
                disabled={syncingClassroom}
              >
                <IconClassroom />
                <span>{syncingClassroom ? 'Syncing Classroom...' : 'Sync Classroom'}</span>
              </button>

              <button
                type="button"
                className="btn btn-secondary"
                onClick={handleReanalyzeAll}
                disabled={reanalyzingAll}
              >
                <IconSync />
                <span>{reanalyzingAll ? 'Re-analyzing...' : 'Re-analyze all'}</span>
              </button>
            </div>

            <div className="action-toolbar-right">
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={handleResetDemo}
                title="Load sample academic tasks"
              >
                Load Demo Data
              </button>

              {hasDemoItems && (
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  onClick={handleClearDemo}
                >
                  Clear Demo Data
                </button>
              )}

              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  loadItems();
                  loadSyncStatus();
                }}
                disabled={loading}
              >
                Refresh
              </button>
            </div>
          </div>

          {/* Google Connections Strip */}
          <div className="connections-strip">
            <div className="connections-strip-items">
              <div className="connection-item">
                <strong>Gmail Sync:</strong>{' '}
                <span className={syncStatus?.status === 'reconnect_needed' ? 'badge badge-urgent' : 'badge badge-source-gmail'}>
                  {syncStatus?.status === 'reconnect_needed' ? 'Reconnect Needed' : 'Connected (Read-Only)'}
                </span>
                <span style={{ color: 'var(--text-tertiary)', fontSize: '0.8rem' }}>
                  {syncStatus?.last_synced_at ? `Last run: ${formatDateTimeWithTz(syncStatus.last_synced_at)}` : 'Not run yet'}
                </span>
              </div>

              <div className="connection-item">
                <strong>Classroom Sync:</strong>{' '}
                <span className={syncStatus?.classroom_status === 'reconnect_needed' ? 'badge badge-urgent' : 'badge badge-source-classroom'}>
                  {syncStatus?.classroom_status === 'reconnect_needed' ? 'Reconnect Needed' : 'Connected (Read-Only)'}
                </span>
                <span style={{ color: 'var(--text-tertiary)', fontSize: '0.8rem' }}>
                  {syncStatus?.classroom_last_synced_at ? `Last run: ${formatDateTimeWithTz(syncStatus.classroom_last_synced_at)}` : 'Not run yet'}
                </span>
              </div>
            </div>

            {connections && (
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                {connections.gmail.connected && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    style={{ fontSize: '0.75rem' }}
                    onClick={() => handleDisconnect('gmail')}
                  >
                    Disconnect Gmail
                  </button>
                )}
                {connections.classroom.connected && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    style={{ fontSize: '0.75rem' }}
                    onClick={() => handleDisconnect('classroom')}
                  >
                    Disconnect Classroom
                  </button>
                )}
              </div>
            )}
          </div>

          {/* Alert Banners */}
          {syncStatus?.classroom_status === 'reconnect_needed' && (
            <div className="alert-banner alert-banner-warning">
              <div>
                <strong>Google Classroom Reconnect Needed:</strong> Authorization token is missing or expired.
                <div style={{ marginTop: '0.4rem', fontFamily: 'var(--font-mono)', fontSize: '0.82rem', background: 'rgba(0,0,0,0.05)', padding: '0.35rem 0.65rem', borderRadius: '6px' }}>
                  python -m app.integrations.google_auth classroom
                </div>
              </div>
            </div>
          )}

          {hasDemoItems && (
            <div className="alert-banner alert-banner-info">
              <div>
                <strong>Demo Mode Active:</strong> Sample academic emails and classroom assignments are currently populated in your workspace.
              </div>
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <button type="button" className="btn btn-secondary btn-sm" onClick={handleResetDemo}>
                  Reset Demo
                </button>
                <button type="button" className="btn btn-danger btn-sm" onClick={handleClearDemo}>
                  Clear Demo Data
                </button>
              </div>
            </div>
          )}

          {healthError && (
            <div className="alert-banner alert-banner-danger">
              <div>Backend offline: {healthError}</div>
              <button type="button" onClick={() => setHealthError(null)} style={{ cursor: 'pointer', fontWeight: 600 }}>✕</button>
            </div>
          )}

          {syncError && (
            <div className="alert-banner alert-banner-warning">
              <div>Sync issue: {syncError}</div>
              <button type="button" onClick={() => setSyncError(null)} style={{ cursor: 'pointer', fontWeight: 600 }}>✕</button>
            </div>
          )}

          {errorMessage && (
            <div className="alert-banner alert-banner-danger">
              <div>{errorMessage}</div>
              <button type="button" onClick={() => setErrorMessage(null)} style={{ cursor: 'pointer', fontWeight: 600 }}>✕</button>
            </div>
          )}

          {actionSuccess && (
            <div className="alert-banner alert-banner-info">
              <div>{actionSuccess}</div>
              <button type="button" onClick={() => setActionSuccess(null)} style={{ cursor: 'pointer', fontWeight: 600 }}>✕</button>
            </div>
          )}

          {/* Filter & View Bar */}
          <div className="filter-controls-row">
            <div className="priority-tabs">
              <div
                className={`priority-tab ${priorityFilter === 'all' ? 'active' : ''}`}
                onClick={() => setPriorityFilter('all')}
              >
                <span>All</span>
                <span className="priority-tab-count">{counts.all}</span>
              </div>
              <div
                className={`priority-tab ${priorityFilter === 'urgent' ? 'active' : ''}`}
                onClick={() => setPriorityFilter('urgent')}
              >
                <span>Urgent</span>
                <span className="priority-tab-count" style={{ color: '#BE123C' }}>{counts.urgent}</span>
              </div>
              <div
                className={`priority-tab ${priorityFilter === 'important' ? 'active' : ''}`}
                onClick={() => setPriorityFilter('important')}
              >
                <span>Important</span>
                <span className="priority-tab-count" style={{ color: '#B45309' }}>{counts.important}</span>
              </div>
              <div
                className={`priority-tab ${priorityFilter === 'normal' ? 'active' : ''}`}
                onClick={() => setPriorityFilter('normal')}
              >
                <span>Normal</span>
                <span className="priority-tab-count">{counts.normal}</span>
              </div>
            </div>

            <div className="dropdown-filters">
              <input
                type="text"
                placeholder="Search notices & tasks..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="select-input"
                style={{ width: '200px' }}
              />

              <select
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value as 'all' | ItemType)}
                className="select-input"
              >
                <option value="all">All Types</option>
                <option value="task">Tasks</option>
                <option value="information">Information</option>
              </select>

              <select
                value={stateFilter}
                onChange={(e) => setStateFilter(e.target.value as 'all' | ItemState)}
                className="select-input"
              >
                <option value="all">All States</option>
                <option value="needs_review">Needs Review</option>
                <option value="not_started">Not Started</option>
                <option value="in_progress">In Progress</option>
                <option value="completed">Completed</option>
                <option value="cancelled">Cancelled</option>
              </select>

              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={showSnoozed}
                  onChange={(e) => setShowSnoozed(e.target.checked)}
                />
                Show Snoozed
              </label>
            </div>
          </div>

          {/* Items Grid / Cards */}
          <div className="items-grid">
            {loading ? (
              <div className="empty-state">
                <p>Loading your academic workspace...</p>
              </div>
            ) : displayedItems.length === 0 ? (
              <div className="empty-state">
                <div className="empty-state-icon">
                  <IconMail />
                </div>
                <h3>No items match your filter</h3>
                <p>Ingest a new notice, email, or load sample demo data to see deterministic task extraction in action.</p>
                <button type="button" className="btn btn-primary" onClick={handleResetDemo}>
                  Load Demo Fixtures
                </button>
              </div>
            ) : (
              displayedItems.map((item) => {
                const relTime = getRelativeTimeLabel(item.deadline_at);
                const isUrgent = item.effective_priority === 'urgent';
                const isImportant = item.effective_priority === 'important';

                return (
                  <article
                    key={item.id}
                    className={`item-card ${item.effective_priority} ${item.state === 'completed' ? 'completed' : ''}`}
                    onClick={() => setSelectedItem(item)}
                  >
                    <div className="item-card-top">
                      <div className="card-badges-left">
                        <span className={`badge ${isUrgent ? 'badge-urgent' : isImportant ? 'badge-important' : 'badge-normal'}`}>
                          {item.effective_priority}
                        </span>

                        <span className="badge badge-kind">
                          {item.kind}
                        </span>

                        <span className="badge badge-state">
                          {item.state.replace('_', ' ')}
                        </span>

                        <span className={`badge badge-source-${item.source_type}`}>
                          {item.source_type}
                        </span>

                        {item.source_status && (
                          <span className={`badge ${item.source_status === 'TURNED_IN' ? 'badge-turned-in' : 'badge-state'}`}>
                            {item.source_status === 'TURNED_IN' ? 'Turned In' : item.source_status}
                          </span>
                        )}
                      </div>

                      <div style={{ display: 'flex', gap: '0.4rem' }}>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={(e) => handleOpenEdit(item, e)}
                        >
                          Edit
                        </button>
                      </div>
                    </div>

                    <div className="item-card-body">
                      <h3 className="item-title">{item.title}</h3>
                      {item.summary && <p className="item-summary">{item.summary}</p>}

                      <div className="item-metadata-row">
                        {item.deadline_at && (
                          <span className={`metadata-item ${isUrgent ? 'deadline-urgent' : 'deadline-confirmed'}`}>
                            <IconCalendar />
                            <span>{formatDateTimeWithTz(item.deadline_at)} ({relTime.label})</span>
                          </span>
                        )}

                        {item.deadline_status === 'ambiguous' && (
                          <span className="badge badge-important" style={{ fontSize: '0.72rem' }}>
                            <IconAlert />
                            <span>Ambiguous: {item.deadline_text}</span>
                          </span>
                        )}

                        {item.duration_minutes && (
                          <span className="metadata-item latest-start-chip">
                            <IconClock />
                            <span>Duration {item.duration_minutes}m</span>
                          </span>
                        )}

                        {item.next_reminder_at && (
                          <span className="metadata-item" style={{ color: 'var(--accent-purple)' }}>
                            <IconBell />
                            <span>Next: {formatDateTimeWithTz(item.next_reminder_at)} ({item.next_reminder_kind})</span>
                          </span>
                        )}

                        {item.sender && (
                          <span className="metadata-item">
                            From: {item.sender}
                          </span>
                        )}

                        {item.extraction_provider && (
                          <span className="metadata-item" style={{ fontSize: '0.75rem' }}>
                            Engine: {item.extraction_provider}
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="item-card-bottom" onClick={(e) => e.stopPropagation()}>
                      <div className="card-actions-left">
                        {!item.acknowledged_at ? (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => handleAcknowledge(item)}
                          >
                            <IconCheck />
                            <span>Acknowledge</span>
                          </button>
                        ) : (
                          <span style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                            <IconCheck />
                            <span>Acknowledged</span>
                          </span>
                        )}

                        {/* Snooze options */}
                        <div style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => handleSnooze(item, 1)}
                            title="Snooze for 1 hour"
                          >
                            1h
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => handleSnooze(item, 24)}
                            title="Snooze for 24 hours"
                          >
                            24h
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => handleSnooze(item, 'tomorrow')}
                            title="Snooze until tomorrow morning"
                          >
                            Tomorrow
                          </button>
                        </div>
                      </div>

                      <div className="card-actions-right">
                        {item.state !== 'completed' ? (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => handleStateChange(item, 'completed')}
                          >
                            Mark Completed
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => handleStateChange(item, 'in_progress')}
                          >
                            Re-open
                          </button>
                        )}

                        {item.source_type === 'gmail' && item.source_id && (
                          <a
                            href={`https://mail.google.com/mail/u/0/#inbox/${item.source_id}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="btn btn-ghost btn-sm"
                          >
                            Open Gmail ↗
                          </a>
                        )}

                        {item.source_type === 'classroom' && item.links && item.links.length > 0 && (
                          <a
                            href={item.links[0]}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="btn btn-ghost btn-sm"
                          >
                            Open Classroom ↗
                          </a>
                        )}
                      </div>
                    </div>
                  </article>
                );
              })
            )}
          </div>
        </div>
      </section>

      {/* ====================================================================
          Feature Showcase Section
          ==================================================================== */}
      <section id="features" className="feature-section">
        <div className="section-header">
          <div className="section-label">Engineered for Academic Safety</div>
          <h2 className="section-headline">
            Deterministic extraction, autonomous scheduling, and absolute local privacy.
          </h2>
        </div>

        <div className="two-col-features">
          <div className="feature-card">
            <div className="feature-card-content">
              <h3>Deterministic Deadline Engine</h3>
              <p>
                Unlike generic LLMs that hallucinate dates and time zones, ClassMail J uses a strict rules baseline.
                Durations (e.g. &ldquo;45 minutes&rdquo;) are extracted precisely to schedule latest-start alerts before confirmed deadlines.
              </p>
            </div>
            <div className="feature-card-preview">
              &gt; Midterm Exam: Nov 15 at 14:00 UTC<br />
              &gt; Duration: 45 minutes<br />
              &gt; Latest Start Trigger: 13:15:00 UTC (exact countdown)
            </div>
          </div>

          <div className="feature-card">
            <div className="feature-card-content">
              <h3>Unified Read-Only Google Sync</h3>
              <p>
                ClassMail J queries your active courses, coursework due dates, announcements, and student submission states.
                All OAuth scopes are strictly read-only, preventing any write actions to your student account.
              </p>
            </div>
            <div className="feature-card-preview">
              &gt; Scopes: classroom.courses.readonly, coursework.me.readonly<br />
              &gt; Submissions: TURNED_IN detected automatically<br />
              &gt; Deduplication by (source_type, source_id)
            </div>
          </div>

          <div className="feature-card">
            <div className="feature-card-content">
              <h3>Autonomous Background Reminders</h3>
              <p>
                A single APScheduler instance runs inside the FastAPI lifespan. Time-critical assessment alerts use one-shot
                DateTrigger jobs with frequency caps, quiet hours, and privacy-preserving push notifications via ntfy.
              </p>
            </div>
            <div className="feature-card-preview">
              &gt; Frequency Cap: Max 5 notifications per rolling hour<br />
              &gt; Quiet Hours: 22:00 - 07:00 (Urgent bypass available)<br />
              &gt; Channel: ntfy push alerts with zero message body leaks
            </div>
          </div>

          <div className="feature-card">
            <div className="feature-card-content">
              <h3>100% Local-First Architecture</h3>
              <p>
                All data resides on your machine in a stdlib SQLite database. With optional Ollama integration,
                local models like Llama 3 improve titles and summaries with strict prompt injection guardrails and seamless rules fallback.
              </p>
            </div>
            <div className="feature-card-preview">
              &gt; Database: backend/data/classmail.db (SQLite stdlib)<br />
              &gt; Engine: Optional Ollama (POST /api/generate @ localhost)<br />
              &gt; Fallback: 100% operational with RulesProvider
            </div>
          </div>
        </div>

        {/* Micro-Features Grid */}
        <div id="architecture" className="micro-features-grid">
          <div className="micro-card">
            <div className="micro-icon"><IconShield /></div>
            <h4>Zero Cloud Leakage</h4>
            <p>Your academic notices and assignments never leave your localhost or reach third-party servers.</p>
          </div>

          <div className="micro-card">
            <div className="micro-icon"><IconClock /></div>
            <h4>Sub-millisecond Search</h4>
            <p>Filter instantly across courses, states, deadlines, and urgency tiers with local indexes.</p>
          </div>

          <div className="micro-card">
            <div className="micro-icon"><IconClassroom /></div>
            <h4>Audit Trail</h4>
            <p>Full item event history preserves original text evidence, timeline mutations, and manual edits.</p>
          </div>

          <div className="micro-card">
            <div className="micro-icon"><IconCheck /></div>
            <h4>Noise-Free Focus</h4>
            <p>Classifies announcements, recruiter messages, routine updates, and exams into actionable priorities.</p>
          </div>
        </div>
      </section>

      {/* ====================================================================
          Final CTA Section
          ==================================================================== */}
      <section className="final-cta-section">
        <div className="final-cta-container">
          <h2>Take control of your academic schedule.</h2>
          <p>
            Deterministic task management built for students and educators. Keep your data private, your deadlines confirmed, and your study time on track.
          </p>

          <div className="final-cta-buttons">
            <button
              type="button"
              className="btn btn-cta-light btn-lg"
              onClick={() => {
                const el = document.getElementById('workspace');
                el?.scrollIntoView({ behavior: 'smooth' });
              }}
            >
              Go to Workspace
            </button>

            <button
              type="button"
              className="btn btn-cta-ghost btn-lg"
              onClick={handleResetDemo}
            >
              Load Demo Fixtures
            </button>
          </div>
        </div>
      </section>

      {/* ====================================================================
          Site Footer
          ==================================================================== */}
      <footer className="site-footer">
        <div className="footer-inner">
          <div className="footer-left">
            <div className="brand-icon" style={{ width: '24px', height: '24px', fontSize: '0.8rem' }}>J</div>
            <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>ClassMail J</span>
            <span className="footer-text">Local-First Academic Assistant • v0.1.0</span>
          </div>

          <div className="footer-links">
            <a href="#workspace">Workspace</a>
            <a href="#features">Features</a>
            <a href="#architecture">Architecture</a>
            <span style={{ color: 'var(--text-tertiary)' }}>Timezone: {localTimezone}</span>
          </div>
        </div>
      </footer>

      {/* ====================================================================
          Selected Item Detail Modal
          ==================================================================== */}
      {selectedItem && (
        <div className="modal-overlay" onClick={() => setSelectedItem(null)} role="dialog" aria-modal="true">
          <div className="modal-card modal-card-lg" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <span className={`badge ${selectedItem.effective_priority === 'urgent' ? 'badge-urgent' : selectedItem.effective_priority === 'important' ? 'badge-important' : 'badge-normal'}`}>
                  {selectedItem.effective_priority}
                </span>
                <span className="badge badge-kind" style={{ marginLeft: '0.4rem' }}>
                  {selectedItem.kind}
                </span>
                <span className={`badge badge-source-${selectedItem.source_type}`} style={{ marginLeft: '0.4rem' }}>
                  {selectedItem.source_type}
                </span>
              </div>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setSelectedItem(null)}
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              <h2 style={{ fontSize: '1.45rem', fontWeight: 700, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>
                {selectedItem.title}
              </h2>

              {selectedItem.summary && (
                <p style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                  {selectedItem.summary}
                </p>
              )}

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '0.85rem', padding: '1rem', background: '#F7F7F5', borderRadius: '12px', fontSize: '0.85rem' }}>
                <div>
                  <strong>Deadline:</strong>{' '}
                  {selectedItem.deadline_at ? formatDateTimeWithTz(selectedItem.deadline_at) : 'None'}
                  {selectedItem.deadline_status === 'ambiguous' && ' (Ambiguous)'}
                </div>

                {selectedItem.duration_minutes && (
                  <div>
                    <strong>Duration:</strong> {selectedItem.duration_minutes} minutes
                  </div>
                )}

                <div>
                  <strong>State:</strong> {selectedItem.state.replace('_', ' ')}
                </div>

                <div>
                  <strong>Extraction Engine:</strong>{' '}
                  <span className="badge badge-kind">{selectedItem.extraction_provider || 'rules'}</span>
                </div>

                {selectedItem.sender && (
                  <div>
                    <strong>Sender:</strong> {selectedItem.sender}
                  </div>
                )}

                <div>
                  <strong>Received:</strong> {formatDateTimeWithTz(selectedItem.received_at)}
                </div>
              </div>

              {selectedItem.priority_reason && (
                <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', background: '#F8FAFC', padding: '0.75rem', borderRadius: '8px', border: '1px solid #E2E8F0' }}>
                  <strong>Priority Reason:</strong> {selectedItem.priority_reason}
                </div>
              )}

              {/* Reminders list */}
              {itemReminders.length > 0 && (
                <div style={{ marginTop: '0.5rem' }}>
                  <h4 style={{ fontSize: '0.9rem', fontWeight: 600, marginBottom: '0.4rem' }}>Scheduled Reminders:</h4>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                    {itemReminders.map((rem) => (
                      <div key={rem.id} style={{ display: 'flex', justifyContent: 'space-between', padding: '0.45rem 0.65rem', background: '#FFFFFF', border: '1px solid var(--border-subtle)', borderRadius: '8px', fontSize: '0.8rem' }}>
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}><IconBell /> {rem.kind}</span>
                        <span>{formatDateTimeWithTz(rem.scheduled_for)} ({rem.status})</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Evidence box */}
              <div>
                <h4 style={{ fontSize: '0.9rem', fontWeight: 600, marginBottom: '0.4rem' }}>Source Evidence:</h4>
                <pre className="evidence-box">
                  {selectedItem.evidence || 'No original text evidence recorded.'}
                </pre>
              </div>
            </div>

            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => handleReanalyzeItem(selectedItem.id)}
                disabled={reanalyzingItem}
              >
                {reanalyzingItem ? 'Re-analyzing...' : 'Re-analyze Item'}
              </button>

              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={(e) => {
                  const it = selectedItem;
                  setSelectedItem(null);
                  handleOpenEdit(it, e);
                }}
              >
                Edit Item
              </button>

              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setSelectedItem(null)}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ====================================================================
          Manual Ingest / Paste Modal
          ==================================================================== */}
      {showPasteModal && (
        <div className="modal-overlay" onClick={() => setShowPasteModal(false)} role="dialog" aria-modal="true">
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Ingest Notice / Email</h3>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setShowPasteModal(false)}
              >
                ✕
              </button>
            </div>

            <form onSubmit={handlePasteSubmit} className="modal-body">
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.88rem' }}>
                Paste the plain text of an academic email, syllabus excerpt, or announcement. Deadlines and priorities will be deterministically computed.
              </p>

              <div className="form-group">
                <label className="form-label">Subject / Title (optional)</label>
                <input
                  type="text"
                  placeholder="e.g. Midterm Announcement CS 101"
                  value={pasteSourceTitle}
                  onChange={(e) => setPasteSourceTitle(e.target.value)}
                  className="form-input"
                />
              </div>

              <div className="form-group">
                <label className="form-label">Sender (optional)</label>
                <input
                  type="text"
                  placeholder="e.g. Professor Smith"
                  value={pasteSender}
                  onChange={(e) => setPasteSender(e.target.value)}
                  className="form-input"
                />
              </div>

              <div className="form-group">
                <label className="form-label">Notice / Email Body *</label>
                <textarea
                  required
                  placeholder="Paste message contents here..."
                  value={pasteEvidence}
                  onChange={(e) => setPasteEvidence(e.target.value)}
                  className="form-textarea"
                />
              </div>

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowPasteModal(false)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={pasteSubmitting}
                >
                  {pasteSubmitting ? 'Ingesting...' : 'Ingest & Compute Priority'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ====================================================================
          Edit Item Modal
          ==================================================================== */}
      {editingItem && (
        <div className="modal-overlay" onClick={() => setEditingItem(null)} role="dialog" aria-modal="true">
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Edit Item</h3>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setEditingItem(null)}
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              <div className="form-group">
                <label className="form-label">Title</label>
                <input
                  type="text"
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  className="form-input"
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                <div className="form-group">
                  <label className="form-label">Kind</label>
                  <select
                    value={editKind}
                    onChange={(e) => setEditKind(e.target.value as ItemKind)}
                    className="form-input"
                  >
                    <option value="assignment">Assignment</option>
                    <option value="assessment">Assessment</option>
                    <option value="announcement">Announcement</option>
                    <option value="interview">Interview</option>
                    <option value="meeting">Meeting</option>
                    <option value="event">Event</option>
                    <option value="recruiter">Recruiter</option>
                    <option value="document_request">Document Request</option>
                    <option value="routine">Routine</option>
                    <option value="other">Other</option>
                  </select>
                </div>

                <div className="form-group">
                  <label className="form-label">Type</label>
                  <select
                    value={editItemType}
                    onChange={(e) => setEditItemType(e.target.value as ItemType)}
                    className="form-input"
                  >
                    <option value="task">Task</option>
                    <option value="information">Information</option>
                  </select>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                <div className="form-group">
                  <label className="form-label">Confirmed Deadline</label>
                  <input
                    type="datetime-local"
                    value={editDeadlineAt}
                    onChange={(e) => setEditDeadlineAt(e.target.value)}
                    className="form-input"
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Duration (minutes)</label>
                  <input
                    type="number"
                    placeholder="e.g. 45"
                    value={editDurationMinutes}
                    onChange={(e) => setEditDurationMinutes(e.target.value)}
                    className="form-input"
                  />
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Deadline Text (Display / Ambiguous)</label>
                <input
                  type="text"
                  value={editDeadlineText}
                  onChange={(e) => setEditDeadlineText(e.target.value)}
                  className="form-input"
                />
              </div>

              <div className="form-group">
                <label className="form-label">Priority Override</label>
                <select
                  value={editPriorityOverride}
                  onChange={(e) => setEditPriorityOverride(e.target.value)}
                  className="form-input"
                >
                  <option value="none">None (Automatic Deterministic Priority)</option>
                  <option value="urgent">Urgent</option>
                  <option value="important">Important</option>
                  <option value="normal">Normal</option>
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
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={handleSaveEdit}
                >
                  Save Changes
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ====================================================================
          System Diagnostics Modal
          ==================================================================== */}
      {showSystemStatusModal && (
        <div className="modal-overlay" onClick={() => setShowSystemStatusModal(false)} role="dialog" aria-modal="true">
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>System Diagnostics</h3>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setShowSystemStatusModal(false)}
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                Diagnostic verification of local services and integrations. No credentials or tokens are exposed.
              </p>

              {loadingSystemStatus ? (
                <div style={{ textAlign: 'center', padding: '2rem' }}>Inspecting local services...</div>
              ) : systemStatus ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.65rem', margin: '0.5rem 0' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
                    <strong>Database Health:</strong>
                    <span className={`badge ${systemStatus.database_ok ? 'badge-source-classroom' : 'badge-urgent'}`}>
                      {systemStatus.database_ok ? 'SQLite Connected' : 'Database Error'}
                    </span>
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
                    <strong>Gmail OAuth Token:</strong>
                    <span className={`badge ${systemStatus.gmail_token_present ? 'badge-source-gmail' : 'badge-important'}`}>
                      {systemStatus.gmail_token_present ? 'Authorized' : 'Reconnect Needed'}
                    </span>
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
                    <strong>Classroom OAuth Token:</strong>
                    <span className={`badge ${systemStatus.classroom_token_present ? 'badge-source-classroom' : 'badge-important'}`}>
                      {systemStatus.classroom_token_present ? 'Authorized' : 'Reconnect Needed'}
                    </span>
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
                    <strong>Push Notifications (ntfy):</strong>
                    <span className={`badge ${systemStatus.ntfy_configured ? 'badge-kind' : 'badge-state'}`}>
                      {systemStatus.ntfy_configured ? 'Configured' : 'Local Only'}
                    </span>
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
                    <strong>Extraction Engine:</strong>
                    <span className="badge badge-kind">
                      {systemStatus.ollama ? systemStatus.ollama.provider.toUpperCase() : 'RULES'}
                    </span>
                  </div>

                  {systemStatus.ollama && systemStatus.ollama.provider === 'ollama' && (
                    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
                      <strong>Ollama Model Status:</strong>
                      <span className={`badge ${systemStatus.ollama.reachable && systemStatus.ollama.model_present ? 'badge-source-classroom' : 'badge-important'}`}>
                        {systemStatus.ollama.reachable
                          ? (systemStatus.ollama.model_present
                              ? `${systemStatus.ollama.configured_model} Ready`
                              : `Missing ${systemStatus.ollama.configured_model}`)
                          : 'Unreachable (Rules Fallback)'}
                      </span>
                    </div>
                  )}

                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
                    <strong>Scheduler (APScheduler):</strong>
                    <span className={`badge ${systemStatus.scheduler_running ? 'badge-source-classroom' : 'badge-state'}`}>
                      {systemStatus.scheduler_running ? 'Running' : 'Stopped'}
                    </span>
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', fontSize: '0.85rem' }}>
                    <strong>ClassMail J Version:</strong>
                    <span>v{systemStatus.version}</span>
                  </div>
                </div>
              ) : (
                <p>Failed to load system diagnostics.</p>
              )}

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={loadSystemStatus}
                  disabled={loadingSystemStatus}
                >
                  Refresh Diagnostics
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => setShowSystemStatusModal(false)}
                >
                  Done
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ====================================================================
          Reminders & Notifications Modal
          ==================================================================== */}
      {showNotificationsModal && (
        <div className="modal-overlay" onClick={() => setShowNotificationsModal(false)} role="dialog" aria-modal="true">
          <div className="modal-card modal-card-lg" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Reminders & Push Alerts</h3>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setShowNotificationsModal(false)}
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              {notifStatus && (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.85rem 1rem', background: '#F7F7F5', borderRadius: '12px', fontSize: '0.85rem' }}>
                  <div>
                    <strong>Push Channel:</strong> {notifStatus.ntfy_configured ? 'ntfy.sh (Configured)' : 'Local Only'}<br />
                    <span style={{ color: 'var(--text-secondary)' }}>
                      Quiet Hours: {notifStatus.quiet_hours_start} - {notifStatus.quiet_hours_end} (Urgent bypass: {notifStatus.urgent_bypass_quiet ? 'Yes' : 'No'})
                    </span>
                  </div>

                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={handleToggleNotifications}
                      disabled={togglingNotif}
                    >
                      {notifStatus.notifications_enabled ? 'Pause Alerts' : 'Enable Alerts'}
                    </button>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      onClick={handleSendTestNotification}
                      disabled={testingNotification}
                    >
                      {testingNotification ? 'Sending...' : 'Send Test Alert'}
                    </button>
                  </div>
                </div>
              )}

              {testNotifResult && (
                <div style={{ padding: '0.65rem 1rem', background: testNotifResult.status === 'ok' ? '#ECFDF5' : '#FEF2F2', borderRadius: '8px', fontSize: '0.85rem' }}>
                  Result: {testNotifResult.outcome} ({testNotifResult.channel})
                </div>
              )}

              <h4 style={{ fontSize: '0.9rem', fontWeight: 600, marginTop: '0.5rem' }}>Recent Dispatched Notifications:</h4>
              <div style={{ maxHeight: '280px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                {notificationLogs.length === 0 ? (
                  <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>No recent notification activity.</p>
                ) : (
                  notificationLogs.map((log) => (
                    <div key={log.id} style={{ padding: '0.65rem 0.85rem', background: '#FFFFFF', border: '1px solid var(--border-subtle)', borderRadius: '8px', fontSize: '0.82rem' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.2rem' }}>
                        <strong>{log.title}</strong>
                        <span style={{ color: 'var(--text-tertiary)' }}>{formatDateTimeWithTz(log.at)}</span>
                      </div>
                      <div style={{ color: 'var(--text-secondary)' }}>{log.body}</div>
                    </div>
                  ))
                )}
              </div>

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => setShowNotificationsModal(false)}
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
