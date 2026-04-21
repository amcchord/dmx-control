import React, { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { useToast } from "../toast";

export default function Login() {
  const { authenticated, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const toast = useToast();

  useEffect(() => {
    if (authenticated) {
      const from = (location.state as { from?: string } | null)?.from || "/";
      navigate(from, { replace: true });
    }
  }, [authenticated, navigate, location.state]);

  if (authenticated) {
    return <Navigate to="/" replace />;
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const ok = await login(password);
      if (!ok) {
        toast.push("Incorrect password", "error");
      }
    } catch (err) {
      toast.push(String(err), "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/2 top-1/2 h-[720px] w-[720px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-gradient-to-br from-[#7c4dff] via-[#ff2daa] to-[#00e5ff] opacity-[0.18] blur-3xl" />
      </div>
      <form
        onSubmit={onSubmit}
        className="card relative w-full max-w-sm p-7"
        autoComplete="off"
      >
        <div className="mb-6 flex items-center gap-3">
          <div className="grid h-10 w-10 grid-cols-2 grid-rows-2 gap-0.5 overflow-hidden rounded-lg">
            <span className="bg-[#7c4dff]" />
            <span className="bg-[#00e5ff]" />
            <span className="bg-[#2ef9b6]" />
            <span className="bg-[#ffb36b]" />
          </div>
          <div>
            <div className="text-lg font-semibold">DMX Control</div>
            <div className="text-xs text-muted">Art-Net lighting console</div>
          </div>
        </div>
        <label htmlFor="pw" className="label">
          Password
        </label>
        <input
          id="pw"
          type="password"
          className="input mt-1"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
          required
        />
        <button
          className="btn-primary mt-5 w-full"
          type="submit"
          disabled={submitting || !password}
        >
          {submitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}
