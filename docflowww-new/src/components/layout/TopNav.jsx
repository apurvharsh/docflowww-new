import React, { useState, useRef, useEffect } from 'react';
import { StickyNote, ShieldCheck, LogOut, ChevronDown, User, Command, CircleHelp, Bell, X } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { notificationsApi } from '../../lib/api';

const TopNav = ({ onTutorialOpen }) => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const [faqOpen, setFaqOpen] = useState(false);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const menuRef = useRef(null);
  const notificationRef = useRef(null);
  const notificationDismissedKey = `docflow_notifications_dismissed_${user?.user_id || user?.username || 'guest'}`;

  useEffect(() => {
    const onClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
      if (notificationRef.current && !notificationRef.current.contains(e.target)) setNotificationOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  const loadNotifications = async () => {
    try {
      const dismissed = JSON.parse(window.localStorage.getItem(notificationDismissedKey) || '[]');
      const data = await notificationsApi.list();
      const visibleNotifications = (data.notifications || []).filter(
        (notification) => !dismissed.includes(notification.log_id)
      );
      setNotifications(visibleNotifications);
      setUnreadCount(visibleNotifications.length);
    } catch {
      // Notifications should not interrupt the main workspace.
    }
  };

  useEffect(() => {
    if (!user) return undefined;
    loadNotifications();
    const interval = window.setInterval(loadNotifications, 10 * 1000);
    window.addEventListener('focus', loadNotifications);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener('focus', loadNotifications);
    };
  }, [user?.user_id, user?.username]);

  const openNotifications = () => {
    const nextOpen = !notificationOpen;
    setNotificationOpen(nextOpen);
    if (nextOpen) {
      loadNotifications();
    }
  };

  const dismissNotification = (logId) => {
    const dismissed = JSON.parse(window.localStorage.getItem(notificationDismissedKey) || '[]');
    if (!dismissed.includes(logId)) {
      window.localStorage.setItem(notificationDismissedKey, JSON.stringify([...dismissed, logId]));
    }
    setNotifications((current) => current.filter((notification) => notification.log_id !== logId));
    setUnreadCount((count) => Math.max(0, count - 1));
  };

  const clearAllNotifications = () => {
    const dismissed = JSON.parse(window.localStorage.getItem(notificationDismissedKey) || '[]');
    const allIds = notifications.map((notification) => notification.log_id);
    window.localStorage.setItem(
      notificationDismissedKey,
      JSON.stringify([...new Set([...dismissed, ...allIds])])
    );
    setNotifications([]);
    setUnreadCount(0);
  };

  const notificationTitle = (notification) => {
    if (
      notification.action === 'ASSIGN_PROJECT_ACCESS'
      && notification.resource_id === user?.user_id
    ) {
      return 'You were granted project access';
    }
    if (
      notification.action === 'ASSIGN_ACCESS'
      && notification.resource_id === user?.user_id
    ) {
      return 'You were granted organization admin access';
    }
    return notification.action.replaceAll('_', ' ');
  };

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <nav className="h-14 border-b border-border bg-surface/95 backdrop-blur flex items-center justify-between px-6 sticky top-0 z-40">
      <div className="flex items-center gap-8">
        <Link to="/" className="flex items-center gap-2.5 text-xl font-bold tracking-tight bg-gemini-gradient bg-clip-text text-transparent">
          <Command className="text-primary" size={19} /> DocFlow AI
        </Link>
        <div className="hidden md:flex items-center gap-5 text-sm text-gray-500">
          <Link to="/" className="hover:text-primary transition-colors">Projects</Link>
          <Link to="/studio" className="hover:text-primary transition-colors">Studio</Link>
          <Link to="/notes" className="hover:text-primary transition-colors">Notes</Link>
          {user?.is_org_admin && <Link to="/admin" className="hover:text-primary transition-colors">Admin</Link>}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Link
          to="/notes"
          className="p-2 text-gray-500 hover:text-primary transition-colors rounded-full hover:bg-surface-hover"
          title="Personal notes"
        >
          <StickyNote size={20} />
        </Link>
        <button
          onClick={onTutorialOpen}
          className="p-2 text-gray-500 hover:text-primary transition-colors rounded-full hover:bg-surface-hover"
          title="Open tutorial"
          aria-label="Open tutorial"
        >
          <CircleHelp size={20} />
        </button>
        <div className="relative" ref={notificationRef}>
          <button
            onClick={openNotifications}
            className="relative p-2 text-gray-500 hover:text-primary transition-colors rounded-full hover:bg-surface-hover"
            title="Notifications"
            aria-label="Notifications"
          >
            <Bell size={20} />
            {unreadCount > 0 && (
              <span className="absolute -right-0.5 -top-0.5 min-w-4 h-4 px-1 rounded-full bg-red-500 text-[10px] leading-4 text-white text-center">
                {unreadCount > 99 ? '99+' : unreadCount}
              </span>
            )}
          </button>
          {notificationOpen && (
            <div className="absolute right-0 mt-2 w-80 max-w-[calc(100vw-2rem)] bg-surface border border-border rounded-lg shadow-lg overflow-hidden z-[70]">
              <div className="px-4 py-3 border-b border-border/50 flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-gray-100">Notifications</p>
                  <p className="text-xs text-gray-500 mt-1">
                    {user?.is_org_admin ? 'All recent organization activity.' : 'Activity from your assigned projects.'}
                  </p>
                </div>
                {notifications.length > 0 && (
                  <button
                    type="button"
                    onClick={clearAllNotifications}
                    className="shrink-0 rounded-md p-1 text-gray-400 hover:bg-red-500/10 hover:text-red-300 transition-colors"
                    title="Clear all visible notifications"
                    aria-label="Clear all visible notifications"
                  >
                    <X size={16} />
                  </button>
                )}
              </div>
              <div className="max-h-80 overflow-y-auto">
                {notifications.length === 0 ? (
                  <p className="px-4 py-6 text-sm text-gray-500 text-center">No recent notifications.</p>
                ) : notifications.map((notification) => (
                  <div key={notification.log_id} className="px-4 py-3 border-b border-border/30 last:border-0">
                    <div className="flex items-start justify-between gap-2">
                      <span className={`text-xs font-semibold ${notification.resource_id === user?.user_id ? 'text-emerald-400' : 'text-primary-light'}`}>
                        {notificationTitle(notification)}
                      </span>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-[10px] text-gray-600">{new Date(notification.timestamp).toLocaleString()}</span>
                        <button
                          type="button"
                          onClick={() => dismissNotification(notification.log_id)}
                          className="text-gray-500 hover:text-gray-200 transition-colors"
                          title="Clear notification"
                          aria-label="Clear notification"
                        >
                          <X size={13} />
                        </button>
                      </div>
                    </div>
                    <p className="text-xs text-gray-400 mt-1">{notification.actor_name}{notification.details ? ` · ${notification.details}` : ''}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
        {user?.is_org_admin && (
          <Link
            to="/admin"
            className="p-2 text-gray-500 hover:text-primary transition-colors rounded-full hover:bg-surface-hover"
            title="Admin"
          >
            <ShieldCheck size={20} />
          </Link>
        )}

        <div className="relative ml-2" ref={menuRef}>
          <button
            type="button"
            aria-label={`Open profile for ${user?.full_name || user?.username || 'Account'}`}
            onClick={() => {
              setMenuOpen((v) => !v);
              setFaqOpen(false);
            }}
            className="flex items-center gap-2 pl-1 pr-2 py-1 rounded-full hover:bg-surface-hover transition-colors"
          >
              <div className="w-7 h-7 rounded-full bg-primary/20 text-primary-light flex items-center justify-center">
              <User size={16} />
            </div>
            <span className="text-sm text-gray-300 max-w-[140px] truncate hidden sm:inline">
              {user?.full_name || user?.username || 'Account'}
            </span>
            <ChevronDown size={14} className="text-gray-500" />
          </button>

          {menuOpen && (
            <div className="absolute right-0 mt-2 w-56 max-w-[calc(100vw-2rem)] bg-surface border border-border rounded-lg shadow-lg overflow-visible z-[70]">
              <div className="px-4 py-3 border-b border-border/50">
                <p className="text-sm font-medium text-primary-light underline underline-offset-2 truncate">{user?.full_name || user?.username}</p>
                <p className="text-xs text-gray-500 truncate">{user?.username}</p>
                <p className="text-xs text-gray-300 truncate mt-1">{user?.organization || 'Organization'}</p>
                <p className="text-xs text-primary-light mt-1">{user?.role}{user?.is_org_admin ? ' · org admin' : ''}</p>
              </div>
              <button
                type="button"
                onClick={() => setFaqOpen((open) => !open)}
                aria-expanded={faqOpen}
                className="w-full px-4 py-2.5 text-left text-sm text-primary-light hover:bg-surface-hover transition-colors"
              >
                FAQ
              </button>
              {faqOpen && (
                <div className="absolute right-0 top-full mt-2 w-80 max-w-[calc(100vw-2rem)] bg-surface border border-border rounded-lg shadow-lg overflow-hidden">
                  <div className="px-4 py-3 border-b border-border/50">
                    <p className="text-sm font-semibold text-gray-100">Access FAQ</p>
                    <p className="text-xs text-gray-500 mt-1">What each role can do in DocFlow.</p>
                  </div>
                  <div className="p-3">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-background/60 text-gray-500">
                        <tr>
                          <th className="px-2 py-1.5 font-medium">Role</th>
                          <th className="px-2 py-1.5 font-medium">Access</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border/40 text-gray-400">
                        <tr><td className="px-2 py-2 text-gray-300">Member</td><td className="px-2 py-2">View and upload documents</td></tr>
                        <tr><td className="px-2 py-2 text-gray-300">Project Admin</td><td className="px-2 py-2">Review and approve documents</td></tr>
                        <tr><td className="px-2 py-2 text-gray-300">Admin</td><td className="px-2 py-2">Manage assigned project access</td></tr>
                        <tr><td className="px-2 py-2 text-gray-300">Org admin</td><td className="px-2 py-2">Manage users, projects, and access</td></tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              <button
                onClick={handleLogout}
                className="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-gray-300 hover:bg-surface-hover hover:text-red-400 transition-colors"
              >
                <LogOut size={16} />
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </nav>
  );
};

export default TopNav;
