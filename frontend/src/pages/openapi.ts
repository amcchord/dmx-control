// Small helper module used by pages/ApiDocs.tsx to walk an OpenAPI 3.x
// document, render human-readable schema summaries, build plausible JSON
// examples, and format curl snippets. Kept dependency-free on purpose.

export type OpenApiDoc = {
  openapi?: string;
  info?: { title?: string; version?: string; description?: string };
  paths?: Record<string, Record<string, OpenApiOperation | unknown>>;
  components?: { schemas?: Record<string, SchemaObject> };
};

export type OpenApiOperation = {
  summary?: string;
  description?: string;
  tags?: string[];
  operationId?: string;
  parameters?: ParameterObject[];
  requestBody?: RequestBodyObject;
  responses?: Record<string, ResponseObject>;
};

export type ParameterObject = {
  name: string;
  in: "query" | "header" | "path" | "cookie";
  required?: boolean;
  description?: string;
  schema?: SchemaObject;
  example?: unknown;
};

export type RequestBodyObject = {
  description?: string;
  required?: boolean;
  content?: Record<string, { schema?: SchemaObject; example?: unknown }>;
};

export type ResponseObject = {
  description?: string;
  content?: Record<string, { schema?: SchemaObject; example?: unknown }>;
};

export type SchemaObject = {
  $ref?: string;
  type?: string | string[];
  format?: string;
  description?: string;
  title?: string;
  enum?: unknown[];
  default?: unknown;
  example?: unknown;
  nullable?: boolean;
  properties?: Record<string, SchemaObject>;
  required?: string[];
  items?: SchemaObject;
  additionalProperties?: boolean | SchemaObject;
  allOf?: SchemaObject[];
  anyOf?: SchemaObject[];
  oneOf?: SchemaObject[];
};

const MAX_DEPTH = 6;

// Chase a local $ref ("#/components/schemas/Foo"). Returns the dereferenced
// schema or the input unchanged if no $ref or it can't be resolved. We only
// support local refs, which is all FastAPI emits.
export function resolveRef(schema: SchemaObject | undefined, doc: OpenApiDoc): SchemaObject {
  if (!schema) return {};
  let cur: SchemaObject = schema;
  let hops = 0;
  while (cur && cur.$ref && hops < MAX_DEPTH) {
    const ref = cur.$ref;
    if (!ref.startsWith("#/")) return cur;
    const parts = ref.slice(2).split("/");
    let node: unknown = doc;
    for (const p of parts) {
      if (node && typeof node === "object" && p in (node as Record<string, unknown>)) {
        node = (node as Record<string, unknown>)[p];
      } else {
        return cur;
      }
    }
    cur = (node as SchemaObject) || {};
    hops += 1;
  }
  return cur;
}

// Merge an allOf into a single flat object schema (best-effort). Any non-object
// member is returned as the first fallback.
function flattenAllOf(schema: SchemaObject, doc: OpenApiDoc): SchemaObject {
  if (!schema.allOf || schema.allOf.length === 0) return schema;
  const merged: SchemaObject = { type: "object", properties: {}, required: [] };
  for (const part of schema.allOf) {
    const r = resolveRef(part, doc);
    if (r.type && r.type !== "object") return r;
    if (r.properties) {
      merged.properties = { ...(merged.properties || {}), ...r.properties };
    }
    if (r.required) {
      merged.required = [...(merged.required || []), ...r.required];
    }
    if (r.description && !merged.description) merged.description = r.description;
  }
  if (schema.description && !merged.description) merged.description = schema.description;
  return merged;
}

// Short human-readable type label for a schema, used inline in property rows.
export function typeLabel(schema: SchemaObject | undefined, doc: OpenApiDoc): string {
  if (!schema) return "any";
  const s = resolveRef(schema, doc);
  if (s.allOf || s.oneOf || s.anyOf) {
    const arr = s.oneOf || s.anyOf || s.allOf || [];
    const parts = arr.map((p) => typeLabel(p, doc));
    const joiner = s.oneOf ? " | " : s.anyOf ? " | " : " & ";
    return parts.join(joiner) || "object";
  }
  if (s.enum) {
    const vals = s.enum.map((v) => JSON.stringify(v)).join(" | ");
    return vals || "enum";
  }
  const t = Array.isArray(s.type) ? s.type.join(" | ") : s.type;
  if (t === "array") {
    return `${typeLabel(s.items, doc)}[]`;
  }
  if (t === "integer") return s.format ? `integer (${s.format})` : "integer";
  if (t === "number") return s.format ? `number (${s.format})` : "number";
  if (t === "string") {
    if (s.format) return `string (${s.format})`;
    return "string";
  }
  if (t === "boolean") return "boolean";
  if (t === "object") {
    const title = s.title;
    if (title) return title;
    return "object";
  }
  if (!t && s.properties) return s.title || "object";
  return t || "any";
}

// Build a plausible JSON example for a schema. Prefers `example` and `default`,
// then falls back to typed defaults.
export function buildExample(
  schema: SchemaObject | undefined,
  doc: OpenApiDoc,
  depth = 0,
  seen: Set<string> = new Set(),
): unknown {
  if (!schema || depth > MAX_DEPTH) return null;
  // Prefer explicit examples before resolving refs, so refs that carry their
  // own example at the ref site win.
  if (schema.example !== undefined) return schema.example;
  if (schema.$ref) {
    if (seen.has(schema.$ref)) return null;
    seen.add(schema.$ref);
  }
  const s = resolveRef(schema, doc);
  if (s.example !== undefined) return s.example;
  if (s.default !== undefined) return s.default;

  const flattened = s.allOf ? flattenAllOf(s, doc) : s;
  const variant = flattened.oneOf?.[0] || flattened.anyOf?.[0];
  if (variant) return buildExample(variant, doc, depth + 1, seen);

  if (flattened.enum && flattened.enum.length > 0) return flattened.enum[0];

  const t = Array.isArray(flattened.type) ? flattened.type[0] : flattened.type;
  if (t === "array") {
    return [buildExample(flattened.items, doc, depth + 1, seen)];
  }
  if (t === "object" || flattened.properties) {
    const out: Record<string, unknown> = {};
    const props = flattened.properties || {};
    const required = new Set(flattened.required || []);
    for (const [key, sub] of Object.entries(props)) {
      // Keep the example compact: only emit required fields at depth > 0.
      if (depth > 0 && !required.has(key) && sub && (sub as SchemaObject).default === undefined) {
        continue;
      }
      out[key] = buildExample(sub, doc, depth + 1, seen);
    }
    return out;
  }
  if (t === "string") {
    if (flattened.format === "date-time") return "2025-01-01T00:00:00Z";
    if (flattened.format === "date") return "2025-01-01";
    if (flattened.format === "uuid") return "00000000-0000-0000-0000-000000000000";
    if (flattened.format === "email") return "user@example.com";
    return "string";
  }
  if (t === "integer") return 0;
  if (t === "number") return 0;
  if (t === "boolean") return true;
  return null;
}

// Produce a `curl` command for an operation. Uses buildExample for the body
// and replaces path templating (`{lid}`) with placeholder values pulled from
// the operation's path parameters. `origin` is usually `window.location.origin`.
export function buildCurl(
  method: string,
  path: string,
  op: OpenApiOperation,
  doc: OpenApiDoc,
  origin: string,
): string {
  const pathParams = (op.parameters || []).filter((p) => p.in === "path");
  let filledPath = path;
  for (const p of pathParams) {
    const example =
      p.example !== undefined
        ? p.example
        : p.schema
          ? buildExample(p.schema, doc)
          : 1;
    const val = typeof example === "string" ? example : JSON.stringify(example);
    filledPath = filledPath.replace(`{${p.name}}`, encodeURIComponent(String(val)));
  }

  const query = (op.parameters || []).filter((p) => p.in === "query" && p.required);
  let qs = "";
  if (query.length > 0) {
    const parts = query.map((p) => {
      const example =
        p.example !== undefined
          ? p.example
          : p.schema
            ? buildExample(p.schema, doc)
            : "";
      return `${encodeURIComponent(p.name)}=${encodeURIComponent(String(example))}`;
    });
    qs = `?${parts.join("&")}`;
  }

  const url = `${origin.replace(/\/$/, "")}${filledPath}${qs}`;
  const methodUpper = method.toUpperCase();

  const lines: string[] = [`curl -X ${methodUpper} '${url}'`];
  lines.push(`  -b cookies.txt -c cookies.txt`);

  const body = op.requestBody?.content?.["application/json"];
  if (body) {
    const example =
      body.example !== undefined ? body.example : buildExample(body.schema, doc);
    const json = JSON.stringify(example, null, 2);
    lines.push(`  -H 'Content-Type: application/json'`);
    lines.push(`  -d '${json.replace(/'/g, "'\\''")}'`);
  }

  return lines.join(" \\\n");
}

// Pull the first response-body schema for 2xx responses, or undefined.
export function firstSuccessSchema(
  op: OpenApiOperation,
  doc: OpenApiDoc,
): { status: string; schema: SchemaObject | undefined; description?: string } | null {
  const responses = op.responses || {};
  const statuses = Object.keys(responses);
  const success =
    statuses.find((s) => s.startsWith("2")) ||
    statuses.find((s) => s === "default") ||
    statuses[0];
  if (!success) return null;
  const resp = responses[success];
  const schema = resp?.content?.["application/json"]?.schema;
  return { status: success, schema, description: resp?.description };
}

// Slugify something suitable for an HTML anchor id.
export function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function endpointAnchor(method: string, path: string): string {
  return `endpoint-${slugify(`${method}-${path}`)}`;
}
