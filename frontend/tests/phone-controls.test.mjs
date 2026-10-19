import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { createServer } from "vite";
import React, { act } from "react";
import { createRoot } from "react-dom/client";

test("Phone blackout and restore require visible confirmation sheets", async () => {
  const dom = new JSDOM(
    '<!doctype html><html><body><div id="test-root"></div></body></html>',
    { url: "http://localhost:8814/" },
  );
  const original = {
    window: globalThis.window,
    document: globalThis.document,
    fetch: globalThis.fetch,
    WebSocket: globalThis.WebSocket,
    Event: globalThis.Event,
  };
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    Event: dom.window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  const posts = [];
  const layer = {
    handle: "demo",
    layer_id: 1,
    effect_id: 1,
    name: "Amber",
    opacity: 1,
    z_index: 100,
    blend_mode: "normal",
    target_channels: ["rgb"],
    mask_light_ids: [1],
  };
  globalThis.fetch = async (path, init = {}) => {
    if (init.method === "POST") posts.push(path);
    let data = [];
    if (path === "/api/auth/status")
      data = {
        authenticated: true,
        user: { id: 1, username: "test", name: "Test", role: "admin" },
      };
    if (path === "/api/looks/history") data = { available: true, entries: [] };
    if (path === "/api/layers") data = [layer];
    if (path === "/api/health")
      data = {
        tick_hz: 30,
        last_tick_ms: 1,
        active_layers: 1,
        dropped_frames: 0,
        tick_count: 100,
      };
    if (init.method === "POST") data = { ok: true };
    return new Response(JSON.stringify(data), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  class Socket {
    constructor() {
      setTimeout(() => this.onopen?.({}), 0);
    }
    close() {}
    send() {}
  }
  globalThis.WebSocket = Socket;
  const vite = await createServer({
    root: new URL("..", import.meta.url).pathname,
    server: { middlewareMode: true, hmr: false },
    appType: "custom",
  });
  const { PanicBar } = await vite.ssrLoadModule(
    "/src/components/shell/AppShell.tsx",
  );
  const { AuthProvider } = await vite.ssrLoadModule("/src/auth.tsx");
  const { ToastProvider } = await vite.ssrLoadModule("/src/toast.tsx");
  const { LayerStoreProvider } = await vite.ssrLoadModule(
    "/src/state/layers.tsx",
  );
  const root = createRoot(document.getElementById("test-root"));
  const button = (label) =>
    [...document.querySelectorAll("button")].find(
      (b) => b.textContent === label,
    );
  const click = async (label) => {
    const b = button(label);
    assert.ok(b, `Button ${label} is present`);
    await act(async () => b.click());
  };
  try {
    await act(async () => {
      root.render(
        React.createElement(
          AuthProvider,
          null,
          React.createElement(
            ToastProvider,
            null,
            React.createElement(
              LayerStoreProvider,
              null,
              React.createElement(PanicBar, { compact: true }),
            ),
          ),
        ),
      );
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    await click("Blackout");
    assert.ok(
      document.querySelector('[role="dialog"]')?.parentNode.parentNode ===
        document.body,
      "sheet escapes the sticky header through a portal",
    );
    assert.equal(posts.length, 0, "opening confirmation cannot change lights");
    await click("Cancel");
    assert.equal(document.querySelector('[role="dialog"]'), null);
    await click("Blackout");
    await click("Blackout whole rig");
    assert.ok(posts.includes("/api/states/blackout/apply"));
    assert.equal(document.querySelector('[role="dialog"]'), null);
    await click("Restore");
    assert.ok(document.querySelector('[role="dialog"]'));
    await click("Restore look");
    assert.ok(posts.includes("/api/looks/restore"));
  } finally {
    await act(async () => root.unmount());
    await vite.close();
    dom.window.close();
    Object.assign(globalThis, original);
  }
});
