import React, { createContext, useContext, useEffect, useState } from "react";
import { Api, ApiError, UserAccount } from "./api";

type AuthState = {
  authenticated: boolean | null; // null = unknown, checking
  user: UserAccount | null;
  canOperate: boolean;
  isAdmin: boolean;
  login: (username: string, password: string) => Promise<boolean>;
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
  const [user, setUser] = useState<UserAccount | null>(null);
  const [authed, setAuthed] = useState<boolean | null>(null);

  const refresh = async () => {
    try {
      const s = await Api.status();
      setAuthed(s.authenticated);
      setUser(s.user);
    } catch {
      setAuthed(false);
    }
  };

  useEffect(() => {
    refresh();
    const expired = () => {
      setAuthed(false);
      setUser(null);
    };
    window.addEventListener("dmx:session-expired", expired);
    return () => window.removeEventListener("dmx:session-expired", expired);
  }, []);

  const login = async (username: string, password: string) => {
    try {
      const r = await Api.login(username, password);
      setAuthed(r.authenticated);
      setUser(r.user);
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
    setUser(null);
  };

  return (
    <Ctx.Provider
      value={{
        authenticated: authed,
        user,
        canOperate: !!user && user.role !== "viewer",
        isAdmin: user?.role === "admin",
        login,
        logout,
        refresh,
      }}
    >
      {children}
    </Ctx.Provider>
  );
}
