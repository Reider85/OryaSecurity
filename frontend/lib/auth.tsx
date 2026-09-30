"use client";

import React, { createContext, useContext, useState, useCallback, useEffect } from "react";

interface AuthContextType {
  token: string | null;
  isAuthenticated: boolean;
  login: (token: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    const stored = localStorage.getItem("scanner_token");
    if (stored) setToken(stored);
  }, []);

  const login = useCallback((newToken: string) => {
    setToken(newToken);
    localStorage.setItem("scanner_token", newToken);
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    localStorage.removeItem("scanner_token");
  }, []);

  return (
    <AuthContext.Provider value={{ token, isAuthenticated: !!token, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within an AuthProvider");
  return context;
}
