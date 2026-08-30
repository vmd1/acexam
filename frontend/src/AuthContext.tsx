import React, { createContext, useContext, useState, useEffect } from 'react';
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

  const refreshSubjects = async () => {
    try {
      const res = await api.get('/auth/me/subjects');
      setHasSubjects(res.data.length > 0);
    } catch (error) {
      setHasSubjects(null);
    }
  };

  useEffect(() => {
    const checkAuth = async () => {
      try {
        const response = await api.get('/auth/me');
        setUser(response.data);
        await refreshSubjects();
      } catch (error) {
        setUser(null);
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
