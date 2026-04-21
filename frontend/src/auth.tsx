import React, { createContext, useContext, useEffect, useState } from "react";
import { Api, ApiError } from "./api";

type AuthState = {
  authenticated: boolean | null; // null = unknown, checking
  login: (password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside provider");
  return v;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [authed, setAuthed] = useState<boolean | null>(null);

  const refresh = async () => {
    try {
      const s = await Api.status();
      setAuthed(s.authenticated);
    } catch {
      setAuthed(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const login = async (password: string) => {
    try {
      const r = await Api.login(password);
      setAuthed(r.authenticated);
      return r.authenticated;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setAuthed(false);
        return false;
      }
      throw e;
    }
  };

  const logout = async () => {
    await Api.logout();
    setAuthed(false);
  };

  return (
    <Ctx.Provider value={{ authenticated: authed, login, logout, refresh }}>
      {children}
    </Ctx.Provider>
  );
}
