import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import api from './api';

interface User {
  id: string;
  email: string;
  display_name: string;
  exam_board?: string;
  year_group?: string;
  is_admin?: boolean;
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
  hasSubjects: boolean | null;
  login: (token: string, userData: User) => Promise<void>;
  logout: () => void;
  refreshSubjects: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [hasSubjects, setHasSubjects] = useState<boolean | null>(null);

  // Track in-flight requests so genuinely concurrent callers (e.g. StrictMode's
  // double-invoked mount effect, or login() racing the mount effect) share one
  // network call instead of firing duplicates. The ref is cleared as soon as the
  // request settles, so any call that happens *after* a previous one has already
  // resolved starts a brand-new fetch rather than reusing stale data.
  const meRequestRef = useRef<Promise<User | null> | null>(null);
  const subjectsRequestRef = useRef<Promise<void> | null>(null);

  const fetchMe = (): Promise<User | null> => {
    if (meRequestRef.current) {
      return meRequestRef.current;
    }
    const request = api
      .get('/auth/me')
      .then((response) => {
        setUser(response.data);
        return response.data as User;
      })
      .catch(() => {
        setUser(null);
        return null;
      })
      .finally(() => {
        meRequestRef.current = null;
      });
    meRequestRef.current = request;
    return request;
  };

  const refreshSubjects = (): Promise<void> => {
    if (subjectsRequestRef.current) {
      return subjectsRequestRef.current;
    }
    const request = api
      .get('/auth/me/subjects')
      .then((res) => {
        setHasSubjects(res.data.length > 0);
      })
      .catch(() => {
        setHasSubjects(null);
      })
      .finally(() => {
        subjectsRequestRef.current = null;
      });
    subjectsRequestRef.current = request;
    return request;
  };

  useEffect(() => {
    const checkAuth = async () => {
      try {
        const me = await fetchMe();
        if (me) {
          await refreshSubjects();
        }
      } finally {
        setLoading(false);
      }
    };
    checkAuth();
  }, []);

  const login = async (_token: string, userData: User) => {
    // Note: token is stored in httpOnly cookie, but we update user state
    setUser(userData);
    await refreshSubjects();
  };

  const logout = async () => {
    try {
      await api.post('/auth/logout');
      setUser(null);
      setHasSubjects(null);
    } catch (error) {
      console.error("Logout failed", error);
    }
  };

  return (
    <AuthContext.Provider value={{ user, loading, hasSubjects, login, logout, refreshSubjects }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
