import React, { useEffect, useState } from "react";
import { Api, UserAccount } from "../api";
import { useAuth } from "../auth";
import { useToast } from "../toast";
import Modal from "../components/Modal";
export default function Accounts() {
  const { user, isAdmin, logout } = useAuth();
  const { push } = useToast();
  const [users, setUsers] = useState<UserAccount[]>([]),
    [open, setOpen] = useState(false),
    [username, setUsername] = useState(""),
    [name, setName] = useState(""),
    [password, setPassword] = useState(""),
    [role, setRole] = useState("operator"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [resetUser, setResetUser] = useState<UserAccount | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const refresh = () =>
    Api.users()
      .then(setUsers)
      .catch((e) => setError(String(e)));
  useEffect(() => {
    if (isAdmin) void refresh();
  }, [isAdmin]);
  if (!isAdmin) return <p>Administrator access required.</p>;
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">People & access</h1>
      <p className="text-sm text-muted">
        Admins configure the rig and manage accounts. Operators run lights and
        save looks. Viewers can watch without changing output.
      </p>
      <button
        className="btn-primary"
        onClick={() => {
          setOpen(true);
          setError("");
          setPassword("");
          setUsername("");
          setName("");
        }}
      >
        Add person
      </button>
      {error && (
        <p role="alert" className="text-rose-200">
          {error}
        </p>
      )}
      {users.map((u) => (
        <section className="card space-y-3 p-4" key={u.id}>
          <div>
            <h2 className="font-semibold">
              {u.name}
              {u.id === user?.id ? " · You" : ""}
            </h2>
            <p className="text-xs text-muted">
              {u.username} · {u.enabled ? "Active" : "Disabled"}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="input !w-auto"
              aria-label={`Role for ${u.username}`}
              value={u.role}
              disabled={u.id === user?.id}
              onChange={async (e) => {
                try {
                  await Api.updateUser(u.id, { role: e.target.value });
                  await refresh();
                } catch (e) {
                  push(String(e), "error");
                }
              }}
            >
              {["admin", "operator", "viewer"].map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
            <button
              className="btn-secondary"
              disabled={u.id === user?.id}
              onClick={async () => {
                if (
                  !window.confirm(
                    `${u.enabled ? "Disable" : "Enable"} ${u.name}'s access?`,
                  )
                )
                  return;
                try {
                  await Api.updateUser(u.id, { enabled: !u.enabled });
                  await refresh();
                } catch (e) {
                  push(String(e), "error");
                }
              }}
            >
              {u.enabled ? "Disable" : "Enable"}
            </button>
            <button
              className="btn-secondary"
              onClick={() => {
                setResetUser(u);
                setNewPassword("");
                setError("");
              }}
            >
              Change password
            </button>
          </div>
        </section>
      ))}
      <Modal
        open={open}
        title="Add person"
        onClose={() => setOpen(false)}
        footer={
          <button form="new-person" className="btn-primary" disabled={busy}>
            {busy ? "Creating…" : "Create account"}
          </button>
        }
      >
        <form
          id="new-person"
          className="space-y-4"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              await Api.createUser({ username, name, password, role });
              setPassword("");
              setOpen(false);
              await refresh();
              push("Account created", "success");
            } catch (e) {
              setError(String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label className="block">
            <span className="label">Name</span>
            <input
              className="input mt-1"
              autoComplete="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </label>
          <label className="block">
            <span className="label">Username</span>
            <input
              className="input mt-1"
              autoCapitalize="none"
              autoComplete="off"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              pattern="[a-zA-Z0-9][a-zA-Z0-9._-]{1,47}"
              required
            />
          </label>
          <label className="block">
            <span className="label">Password · at least 12 characters</span>
            <input
              className="input mt-1"
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={256}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <label className="block">
            <span className="label">Access</span>
            <select
              className="input mt-1"
              value={role}
              onChange={(e) => setRole(e.target.value)}
            >
              {["operator", "viewer", "admin"].map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </label>
          {error && (
            <p role="alert" className="text-rose-200">
              {error}
            </p>
          )}
        </form>
      </Modal>
      <Modal
        open={!!resetUser}
        title={`Change password · ${resetUser?.name || ""}`}
        onClose={() => setResetUser(null)}
        footer={
          <button
            form="change-password"
            className="btn-primary"
            disabled={busy || newPassword.length < 12}
          >
            {busy ? "Saving…" : "Change password"}
          </button>
        }
      >
        <form
          id="change-password"
          className="space-y-4"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!resetUser) return;
            setBusy(true);
            try {
              await Api.updateUser(resetUser.id, { password: newPassword });
              setNewPassword("");
              setResetUser(null);
              push(
                "Password changed. Existing sessions have been signed out.",
                "success",
              );
              if (resetUser.id === user?.id) await logout();
            } catch (e) {
              setError(String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label className="block">
            <span className="label">New password · at least 12 characters</span>
            <input
              className="input mt-1"
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={256}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
            />
          </label>
          <p className="text-sm text-muted">
            All existing sessions for this account will be signed out.
          </p>
          {error && (
            <p role="alert" className="text-rose-200">
              {error}
            </p>
          )}
        </form>
      </Modal>
    </div>
  );
}
