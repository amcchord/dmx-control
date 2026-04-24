import React, { useEffect, useMemo, useState } from "react";
import { useToast } from "../toast";
import {
  OpenApiDoc,
  OpenApiOperation,
  ParameterObject,
  SchemaObject,
  buildCurl,
  buildExample,
  endpointAnchor,
  firstSuccessSchema,
  resolveRef,
  slugify,
  typeLabel,
} from "./openapi";

type Row = {
  method: string;
  path: string;
  op: OpenApiOperation;
  tag: string;
};

const METHOD_ORDER = ["get", "post", "put", "patch", "delete", "options", "head"];

const METHOD_CLASSES: Record<string, string> = {
  get: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  post: "bg-indigo-500/15 text-indigo-300 ring-indigo-500/30",
  patch: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  delete: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  put: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  options: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
  head: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
};

const TAG_ORDER = [
  "auth",
  "controllers",
  "lights",
  "models",
  "palettes",
  "effects",
  "scenes",
  "states",
  "state",
  "ai",
];

const GUIDE_SECTIONS: { id: string; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "base-url", label: "Base URL" },
  { id: "authentication", label: "Authentication" },
  { id: "errors", label: "Errors" },
  { id: "live-state", label: "Live state" },
  { id: "art-net", label: "Art-Net output" },
  { id: "effects-engine", label: "Effects engine" },
];

export default function ApiDocs() {
  const toast = useToast();
  const [doc, setDoc] = useState<OpenApiDoc | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [origin, setOrigin] = useState<string>("");

  useEffect(() => {
    if (typeof window !== "undefined") setOrigin(window.location.origin);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch("/openapi.json", { credentials: "same-origin" })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as OpenApiDoc;
      })
      .then((data) => {
        if (!cancelled) setDoc(data);
      })
      .catch((err) => {
        if (!cancelled) {
          toast.push(`Failed to load /openapi.json: ${String(err)}`, "error");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [toast]);

  const rows: Row[] = useMemo(() => {
    if (!doc?.paths) return [];
    const out: Row[] = [];
    for (const [path, ops] of Object.entries(doc.paths)) {
      for (const method of METHOD_ORDER) {
        const op = (ops as Record<string, unknown>)[method] as
          | OpenApiOperation
          | undefined;
        if (!op || typeof op !== "object") continue;
        const tag = (op.tags && op.tags[0]) || "other";
        out.push({ method, path, op, tag });
      }
    }
    return out;
  }, [doc]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) => {
      const hay = `${r.method} ${r.path} ${r.op.summary || ""} ${
        r.op.description || ""
      } ${r.tag}`.toLowerCase();
      return hay.includes(q);
    });
  }, [rows, filter]);

  const grouped = useMemo(() => {
    const groups = new Map<string, Row[]>();
    for (const r of filtered) {
      const arr = groups.get(r.tag) || [];
      arr.push(r);
      groups.set(r.tag, arr);
    }
    for (const arr of groups.values()) {
      arr.sort((a, b) => {
        if (a.path === b.path) {
          return METHOD_ORDER.indexOf(a.method) - METHOD_ORDER.indexOf(b.method);
        }
        return a.path.localeCompare(b.path);
      });
    }
    const tags = Array.from(groups.keys()).sort((a, b) => {
      const ai = TAG_ORDER.indexOf(a);
      const bi = TAG_ORDER.indexOf(b);
      if (ai === -1 && bi === -1) return a.localeCompare(b);
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    });
    return { groups, tags };
  }, [filtered]);

  return (
    <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
      <Sidebar
        tags={grouped.tags}
        groups={grouped.groups}
        filter={filter}
        onFilterChange={setFilter}
      />
      <div className="min-w-0 flex-1 space-y-10">
        <Header doc={doc} />
        <Guide origin={origin} />
        {loading && (
          <div className="card px-5 py-4 text-sm text-muted">
            Loading /openapi.json...
          </div>
        )}
        {!loading && doc && grouped.tags.length === 0 && (
          <div className="card px-5 py-4 text-sm text-muted">
            No endpoints match "{filter}".
          </div>
        )}
        {doc &&
          grouped.tags.map((tag) => (
            <TagSection
              key={tag}
              tag={tag}
              rows={grouped.groups.get(tag) || []}
              doc={doc}
              origin={origin}
            />
          ))}
      </div>
    </div>
  );
}

function Header({ doc }: { doc: OpenApiDoc | null }) {
  return (
    <header className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-2xl font-semibold text-white">API Reference</h1>
        {doc?.info?.version && (
          <span className="pill">v{doc.info.version}</span>
        )}
        {doc?.openapi && (
          <span className="pill">OpenAPI {doc.openapi}</span>
        )}
      </div>
      <p className="max-w-3xl text-sm text-muted">
        Auto-generated from the backend's{" "}
        <a className="text-accent hover:text-accent-hover" href="/openapi.json">
          /openapi.json
        </a>
        , with hand-written notes for the auth, Art-Net, and effects flows.
        Every endpoint lives under <code className="font-mono">/api</code> and
        returns JSON.
      </p>
    </header>
  );
}

function Sidebar({
  tags,
  groups,
  filter,
  onFilterChange,
}: {
  tags: string[];
  groups: Map<string, Row[]>;
  filter: string;
  onFilterChange: (v: string) => void;
}) {
  return (
    <aside className="lg:sticky lg:top-20 lg:w-64 lg:flex-shrink-0">
      <div className="card space-y-3 p-3">
        <input
          className="input"
          value={filter}
          onChange={(e) => onFilterChange(e.target.value)}
          placeholder="Filter endpoints..."
          aria-label="Filter endpoints"
        />
        <nav className="max-h-[70vh] overflow-y-auto pr-1 text-sm">
          <div className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-muted">
            Guide
          </div>
          <ul className="mb-3 space-y-0.5">
            {GUIDE_SECTIONS.map((s) => (
              <li key={s.id}>
                <a
                  href={`#${s.id}`}
                  className="block rounded px-2 py-1 text-slate-300 hover:bg-bg-elev hover:text-white"
                >
                  {s.label}
                </a>
              </li>
            ))}
          </ul>
          <div className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-muted">
            Endpoints
          </div>
          <ul className="space-y-2">
            {tags.map((tag) => (
              <li key={tag}>
                <a
                  href={`#tag-${slugify(tag)}`}
                  className="block rounded px-2 py-1 font-medium text-slate-200 hover:bg-bg-elev hover:text-white"
                >
                  {tag}
                </a>
                <ul className="ml-1 mt-0.5 space-y-0.5">
                  {(groups.get(tag) || []).map((r) => (
                    <li key={`${r.method}-${r.path}`}>
                      <a
                        href={`#${endpointAnchor(r.method, r.path)}`}
                        className="flex items-center gap-2 rounded px-2 py-1 text-xs text-slate-400 hover:bg-bg-elev hover:text-slate-100"
                      >
                        <MethodBadge method={r.method} small />
                        <span className="truncate font-mono">{r.path}</span>
                      </a>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </aside>
  );
}

function MethodBadge({
  method,
  small = false,
}: {
  method: string;
  small?: boolean;
}) {
  const cls = METHOD_CLASSES[method.toLowerCase()] || METHOD_CLASSES.get;
  return (
    <span
      className={
        "inline-flex items-center justify-center rounded-md font-mono font-semibold uppercase ring-1 " +
        (small ? "min-w-[42px] px-1 py-0.5 text-[10px] " : "min-w-[64px] px-2 py-0.5 text-xs ") +
        cls
      }
    >
      {method}
    </span>
  );
}

function TagSection({
  tag,
  rows,
  doc,
  origin,
}: {
  tag: string;
  rows: Row[];
  doc: OpenApiDoc;
  origin: string;
}) {
  return (
    <section id={`tag-${slugify(tag)}`} className="space-y-4">
      <h2 className="text-xl font-semibold capitalize text-white">{tag}</h2>
      <div className="space-y-4">
        {rows.map((r) => (
          <EndpointCard
            key={`${r.method}-${r.path}`}
            method={r.method}
            path={r.path}
            op={r.op}
            doc={doc}
            origin={origin}
          />
        ))}
      </div>
    </section>
  );
}

function EndpointCard({
  method,
  path,
  op,
  doc,
  origin,
}: {
  method: string;
  path: string;
  op: OpenApiOperation;
  doc: OpenApiDoc;
  origin: string;
}) {
  const anchor = endpointAnchor(method, path);
  const pathParams = (op.parameters || []).filter((p) => p.in === "path");
  const queryParams = (op.parameters || []).filter((p) => p.in === "query");
  const bodySchema = op.requestBody?.content?.["application/json"]?.schema;
  const success = firstSuccessSchema(op, doc);
  const curl = useMemo(
    () => buildCurl(method, path, op, doc, origin || "https://your-host"),
    [method, path, op, doc, origin],
  );

  return (
    <article
      id={anchor}
      className="card scroll-mt-24 overflow-hidden"
    >
      <header className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
        <MethodBadge method={method} />
        <code className="min-w-0 flex-1 truncate font-mono text-sm text-slate-100">
          {path}
        </code>
        <a
          href={`#${anchor}`}
          className="text-xs text-muted hover:text-slate-200"
          title="Permalink"
        >
          #
        </a>
      </header>
      <div className="space-y-5 px-4 py-4">
        {op.summary && (
          <p className="text-sm font-medium text-slate-100">{op.summary}</p>
        )}
        {op.description && (
          <p className="whitespace-pre-wrap text-sm text-muted">
            {op.description}
          </p>
        )}

        {pathParams.length > 0 && (
          <Section label="Path parameters">
            <ParamsTable params={pathParams} doc={doc} />
          </Section>
        )}

        {queryParams.length > 0 && (
          <Section label="Query parameters">
            <ParamsTable params={queryParams} doc={doc} />
          </Section>
        )}

        {bodySchema && (
          <Section label="Request body">
            <SchemaView schema={bodySchema} doc={doc} />
            <JsonBlock
              label="Example"
              value={buildExample(bodySchema, doc)}
            />
          </Section>
        )}

        {success?.schema && (
          <Section label={`Response ${success.status}`}>
            {success.description && (
              <p className="mb-2 text-xs text-muted">{success.description}</p>
            )}
            <SchemaView schema={success.schema} doc={doc} />
            <JsonBlock
              label="Example"
              value={buildExample(success.schema, doc)}
            />
          </Section>
        )}

        {success && !success.schema && (
          <Section label={`Response ${success.status}`}>
            <p className="text-xs text-muted">
              {success.description || "No response body."}
            </p>
          </Section>
        )}

        <Section label="Example request">
          <pre className="overflow-x-auto rounded-lg bg-bg-elev p-3 font-mono text-xs leading-relaxed text-slate-100 ring-1 ring-line">
            {curl}
          </pre>
        </Section>
      </div>
    </article>
  );
}

function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <details className="group" open>
      <summary className="flex cursor-pointer select-none items-center justify-between gap-2 rounded-md bg-bg-elev px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted ring-1 ring-line hover:text-slate-200">
        <span>{label}</span>
        <span className="text-slate-400 transition-transform group-open:rotate-90">
          &rsaquo;
        </span>
      </summary>
      <div className="mt-3 space-y-3">{children}</div>
    </details>
  );
}

function ParamsTable({
  params,
  doc,
}: {
  params: ParameterObject[];
  doc: OpenApiDoc;
}) {
  return (
    <div className="overflow-hidden rounded-lg ring-1 ring-line">
      <table className="w-full border-collapse text-left text-sm">
        <thead className="bg-bg-elev text-xs uppercase tracking-wide text-muted">
          <tr>
            <th className="px-3 py-2 font-semibold">Name</th>
            <th className="px-3 py-2 font-semibold">Type</th>
            <th className="px-3 py-2 font-semibold">Required</th>
            <th className="px-3 py-2 font-semibold">Description</th>
          </tr>
        </thead>
        <tbody>
          {params.map((p, i) => (
            <tr
              key={`${p.in}-${p.name}`}
              className={
                "border-t border-line " +
                (i % 2 === 0 ? "bg-bg-card" : "bg-bg-card/60")
              }
            >
              <td className="px-3 py-2 font-mono text-slate-100">{p.name}</td>
              <td className="px-3 py-2 font-mono text-xs text-slate-300">
                {typeLabel(p.schema, doc)}
              </td>
              <td className="px-3 py-2 text-xs">
                {p.required ? (
                  <span className="text-amber-300">required</span>
                ) : (
                  <span className="text-muted">optional</span>
                )}
              </td>
              <td className="px-3 py-2 text-xs text-muted">
                {p.description || p.schema?.description || ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SchemaView({
  schema,
  doc,
  depth = 0,
}: {
  schema: SchemaObject;
  doc: OpenApiDoc;
  depth?: number;
}) {
  const s = resolveRef(schema, doc);
  if (s.properties || s.type === "object") {
    const props = s.properties || {};
    const required = new Set(s.required || []);
    const entries = Object.entries(props);
    if (entries.length === 0) {
      return (
        <p className="text-xs text-muted">
          Arbitrary object{" "}
          <span className="font-mono">({typeLabel(schema, doc)})</span>
        </p>
      );
    }
    return (
      <ul className="space-y-1 text-sm">
        {entries.map(([name, sub]) => {
          const resolved = resolveRef(sub, doc);
          const nested =
            resolved.type === "object" && resolved.properties && depth < 2;
          const nestedArray =
            resolved.type === "array" &&
            resolveRef(resolved.items || {}, doc).properties &&
            depth < 2;
          return (
            <li
              key={name}
              className="rounded-md border border-line/60 bg-bg-elev/40 px-2 py-1.5"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-slate-100">{name}</span>
                <span className="font-mono text-xs text-slate-400">
                  {typeLabel(sub, doc)}
                </span>
                {required.has(name) ? (
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-amber-300">
                    required
                  </span>
                ) : (
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                    optional
                  </span>
                )}
              </div>
              {(resolved.description || sub.description) && (
                <p className="mt-0.5 text-xs text-muted">
                  {resolved.description || sub.description}
                </p>
              )}
              {nested && (
                <div className="mt-2 border-l border-line/60 pl-3">
                  <SchemaView schema={resolved} doc={doc} depth={depth + 1} />
                </div>
              )}
              {nestedArray && resolved.items && (
                <div className="mt-2 border-l border-line/60 pl-3">
                  <p className="mb-1 text-[10px] uppercase tracking-wide text-muted">
                    Array items
                  </p>
                  <SchemaView
                    schema={resolved.items}
                    doc={doc}
                    depth={depth + 1}
                  />
                </div>
              )}
            </li>
          );
        })}
      </ul>
    );
  }
  if (s.type === "array" && s.items) {
    return (
      <div>
        <p className="mb-1 text-xs text-muted">
          Array of{" "}
          <span className="font-mono">{typeLabel(s.items, doc)}</span>
        </p>
        <SchemaView schema={s.items} doc={doc} depth={depth + 1} />
      </div>
    );
  }
  return (
    <p className="text-xs text-muted">
      <span className="font-mono">{typeLabel(schema, doc)}</span>
      {s.description ? ` — ${s.description}` : ""}
    </p>
  );
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted">
        {label}
      </div>
      <pre className="overflow-x-auto rounded-lg bg-bg-elev p-3 font-mono text-xs leading-relaxed text-slate-100 ring-1 ring-line">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function Guide({ origin }: { origin: string }) {
  const base = origin ? `${origin}/api` : "https://your-host/api";
  return (
    <div className="space-y-6">
      <GuideCard id="overview" title="Overview">
        <p>
          The DMX Control backend is a FastAPI service that speaks JSON over
          HTTP. The SPA you're looking at is served by the same process, so
          same-origin cookies work out of the box. All functional endpoints are
          namespaced under <code className="font-mono">/api</code>; everything
          else is the React app or static assets.
        </p>
        <p>
          Request and response bodies are always JSON unless otherwise noted
          (the one exception is the model-image upload endpoint, which takes
          <code className="font-mono"> multipart/form-data</code>).
        </p>
      </GuideCard>

      <GuideCard id="base-url" title="Base URL">
        <p>
          The API is same-origin with the web UI, so in production it's
          whatever host you reach the SPA on:
        </p>
        <pre className="overflow-x-auto rounded-lg bg-bg-elev p-3 font-mono text-xs text-slate-100 ring-1 ring-line">
          {base}
        </pre>
        <p>
          In the default deployment, Caddy terminates TLS and reverse-proxies
          to the FastAPI process. The session cookie is issued without the
          secure flag because TLS is handled upstream; don't expose the
          backend directly to the public internet.
        </p>
      </GuideCard>

      <GuideCard id="authentication" title="Authentication">
        <p>
          Auth is a single shared password that exchanges for an HttpOnly,
          SameSite=Lax signed session cookie (<code className="font-mono">dmx_session</code>
          by default). The cookie is signed with a server-side secret via{" "}
          <code className="font-mono">itsdangerous</code>; there are no user
          accounts, roles, or tokens.
        </p>
        <p>The full flow is three endpoints:</p>
        <ul className="list-disc space-y-1 pl-5">
          <li>
            <code className="font-mono">POST /api/auth/login</code> — body{" "}
            <code className="font-mono">{'{"password": "..."}'}</code>. 200 on
            success (sets the cookie), 401 on mismatch.
          </li>
          <li>
            <code className="font-mono">GET /api/auth/status</code> — returns{" "}
            <code className="font-mono">{'{"authenticated": true|false}'}</code>.
          </li>
          <li>
            <code className="font-mono">POST /api/auth/logout</code> — clears
            the cookie.
          </li>
        </ul>
        <p>Sample <code className="font-mono">curl</code> flow:</p>
        <pre className="overflow-x-auto rounded-lg bg-bg-elev p-3 font-mono text-xs leading-relaxed text-slate-100 ring-1 ring-line">
{`curl -c cookies.txt -X POST ${base}/auth/login \\
  -H 'Content-Type: application/json' \\
  -d '{"password": "your-password"}'

curl -b cookies.txt ${base}/auth/status

curl -b cookies.txt ${base}/lights`}
        </pre>
        <p>
          Every <code className="font-mono">/api/*</code> endpoint except the
          auth endpoints themselves requires the cookie. Missing or expired
          cookies return{" "}
          <code className="font-mono">401 {'{"detail": "authentication required"}'}</code>.
        </p>
      </GuideCard>

      <GuideCard id="errors" title="Errors">
        <p>
          Handlers raise FastAPI's <code className="font-mono">HTTPException</code>,
          so error bodies are always shaped like:
        </p>
        <pre className="overflow-x-auto rounded-lg bg-bg-elev p-3 font-mono text-xs text-slate-100 ring-1 ring-line">
{`{
  "detail": "human-readable message"
}`}
        </pre>
        <p>Common statuses you'll see:</p>
        <ul className="list-disc space-y-1 pl-5">
          <li>
            <span className="font-mono">400</span> — invalid references or
            semantic constraints (e.g. <em>start_address + channel_count
            exceeds 512</em>, <em>mode does not belong to this model</em>).
          </li>
          <li>
            <span className="font-mono">401</span> — missing or expired session
            cookie.
          </li>
          <li>
            <span className="font-mono">404</span> — object not found (light,
            effect, scene, etc.).
          </li>
          <li>
            <span className="font-mono">422</span> — Pydantic validation
            failure. The <code className="font-mono">detail</code> is a list of
            field-level errors.
          </li>
        </ul>
      </GuideCard>

      <GuideCard id="live-state" title="Live state & rendered output">
        <p>
          Stored state (what's in the database) and <em>rendered</em> state
          (what the Art-Net manager is actually emitting right now) are two
          different things. Two endpoints surface the live view:
        </p>
        <ul className="list-disc space-y-1 pl-5">
          <li>
            <code className="font-mono">GET /api/lights/rendered</code> —
            snapshot of the last frame pushed to the rig, keyed by light id.
            The Dashboard polls this at a high rate while effects are running
            so fixture cards animate in lock-step with the lights.
          </li>
          <li>
            <code className="font-mono">GET /api/state</code> — consolidated
            dashboard payload (controllers, lights, active effects, palettes,
            etc.) intended for a single initial fetch on page load.
          </li>
        </ul>
        <p>
          If you're polling from outside the SPA, treat{" "}
          <code className="font-mono">/api/lights</code> as the source of truth
          for the stored color assignments, and{" "}
          <code className="font-mono">/api/lights/rendered</code> as the feed
          of what the hardware is showing.
        </p>
      </GuideCard>

      <GuideCard id="art-net" title="Art-Net output">
        <p>
          The backend is the only Art-Net speaker in this system. It pushes
          DMX universes to each controller's IP address over UDP 6454. Clients
          of this HTTP API never construct Art-Net packets directly; they
          describe intent (colors, effects, scenes) and the engine turns that
          into DMX frames.
        </p>
        <p>
          Controller records (<code className="font-mono">/api/controllers</code>)
          carry the address and universe layout. Sending a whole-rig blackout
          is just <code className="font-mono">POST /api/controllers/&#123;cid&#125;/blackout</code>,
          which zeros the currently-held frame for that controller.
        </p>
      </GuideCard>

      <GuideCard id="effects-engine" title="Effects engine">
        <p>
          Effects are stored as database rows and executed by a single
          long-running engine in the backend process. You control playback
          with:
        </p>
        <ul className="list-disc space-y-1 pl-5">
          <li>
            <code className="font-mono">POST /api/effects/&#123;eid&#125;/play</code> —
            start (or restart) a stored effect.
          </li>
          <li>
            <code className="font-mono">POST /api/effects/&#123;eid&#125;/stop</code> —
            stop a specific effect.
          </li>
          <li>
            <code className="font-mono">POST /api/effects/stop-all</code> —
            halt every active effect.
          </li>
          <li>
            <code className="font-mono">GET /api/effects/active</code> — list
            running effects, including ones resumed from the previous process
            (effects marked <code className="font-mono">is_active</code> are
            automatically replayed on startup).
          </li>
          <li>
            <code className="font-mono">POST /api/effects/live</code> — start
            an unsaved, in-memory effect and receive a handle.{" "}
            <code className="font-mono">POST /api/effects/live/&#123;handle&#125;/stop</code>{" "}
            stops it and{" "}
            <code className="font-mono">POST /api/effects/live/&#123;handle&#125;/save</code>{" "}
            persists it as a regular effect row.
          </li>
        </ul>
      </GuideCard>
    </div>
  );
}

function GuideCard({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="card scroll-mt-24 px-5 py-4">
      <h2 className="mb-3 text-lg font-semibold text-white">
        <a href={`#${id}`} className="hover:text-accent-hover">
          {title}
        </a>
      </h2>
      <div className="space-y-3 text-sm leading-relaxed text-slate-300">
        {children}
      </div>
    </section>
  );
}
