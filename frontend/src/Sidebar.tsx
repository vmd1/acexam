import { Link, NavLink } from 'react-router-dom';
import {
  GraduationCap,
  ClipboardList,
  History,
  Settings,
  LogOut,
  Sun,
  Moon,
  ChevronsLeft,
  ChevronsRight,
} from 'lucide-react';
import { useAuth } from './AuthContext';

interface NavItem {
  to: string;
  label: string;
  icon: React.ReactNode;
}

type Theme = 'light' | 'dark';

interface SidebarProps {
  theme: Theme;
  onToggleTheme: () => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

function useNavItems(): NavItem[] {
  const { user } = useAuth();
  const items: NavItem[] = [
    { to: '/app', label: 'Practice', icon: <ClipboardList size={20} /> },
  ];
  if (user) items.push({ to: '/app/history', label: 'History', icon: <History size={20} /> });
  if (user?.is_admin) {
    items.push({ to: '/admin/subjects', label: 'Manage Subjects', icon: <Settings size={20} /> });
  }
  return items;
}

// Desktop left rail, collapsible to an icon-only strip. On narrow viewports
// this is hidden entirely via CSS and BottomTabBar renders the same
// destinations as a fixed tab bar instead.
export default function Sidebar({ theme, onToggleTheme, collapsed, onToggleCollapsed }: SidebarProps) {
  const { user, logout } = useAuth();
  const items = useNavItems();

  return (
    <aside className={`sidebar${collapsed ? ' collapsed' : ''}`}>
      <Link to={user ? '/app' : '/'} className="sidebar-brand" title="Acexam">
        <GraduationCap size={24} aria-hidden="true" />
        {!collapsed && 'Acexam'}
      </Link>

      <button
        onClick={onToggleCollapsed}
        className="sidebar-collapse-toggle"
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        aria-expanded={!collapsed}
      >
        {collapsed ? <ChevronsRight size={16} aria-hidden="true" /> : <ChevronsLeft size={16} aria-hidden="true" />}
        {!collapsed && <span>Collapse</span>}
      </button>

      <ul className="sidebar-nav">
        {items.map(item => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.to === '/app'}
              className={({ isActive }) => `sidebar-link${isActive ? ' active' : ''}`}
              title={collapsed ? item.label : undefined}
            >
              {item.icon}
              <span>{item.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>

      <div className="sidebar-footer">
        {user && (
          <Link to="/account" className="sidebar-account" title={collapsed ? user.display_name : undefined}>
            <span>{user.display_name}</span>
          </Link>
        )}
        <div className="sidebar-utility-row">
          <button
            onClick={onToggleTheme}
            className="theme-toggle"
            aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
          >
            {theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}
          </button>
          {user && (
            <button
              onClick={logout}
              className="btn btn-outline"
              style={{ flex: 1, padding: '0.5rem 0.9rem' }}
              aria-label="Log out"
              title={collapsed ? 'Log out' : undefined}
            >
              <LogOut size={16} aria-hidden="true" />
              <span>Log out</span>
            </button>
          )}
        </div>
        {!collapsed && (
          <div className="sidebar-legal-links">
            <Link to="/terms">Terms</Link>
            <span aria-hidden="true">·</span>
            <Link to="/privacy">Privacy</Link>
          </div>
        )}
      </div>
    </aside>
  );
}

// Mobile-only fixed tab bar rendering the same nav destinations as the sidebar.
export function BottomTabBar() {
  const items = useNavItems();

  return (
    <nav className="bottom-tabbar" aria-label="Primary">
      {items.map(item => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === '/app'}
          className={({ isActive }) => `bottom-tabbar-link${isActive ? ' active' : ''}`}
        >
          {item.icon}
          {item.label}
        </NavLink>
      ))}
      <NavLink
        to="/account"
        className={({ isActive }) => `bottom-tabbar-link${isActive ? ' active' : ''}`}
      >
        <GraduationCap size={20} />
        Account
      </NavLink>
    </nav>
  );
}
