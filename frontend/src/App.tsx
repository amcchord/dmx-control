import React from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { ToastProvider } from "./toast";
import AppShell from "./components/shell/AppShell";
import { LayerStoreProvider } from "./state/layers";
import { SelectionProvider } from "./state/selection";
import { UndoProvider } from "./state/undo";

import Login from "./pages/Login";
import NowPlaying from "./pages/operate/NowPlaying";
import LightsOperate from "./pages/operate/Lights";
import QuickFx from "./pages/operate/QuickFx";
import ScenesOperate from "./pages/operate/Scenes";
import Me from "./pages/operate/Me";

import EffectsComposer from "./pages/author/EffectsComposer";
import SceneComposer from "./pages/author/SceneComposer";

import Controllers from "./pages/Controllers";
import ControllerDetail from "./pages/ControllerDetail";
import Models from "./pages/Models";
import ModelEditor from "./pages/ModelEditor";
import Palettes from "./pages/Palettes";
import Designer from "./pages/Designer";
import ApiDocs from "./pages/ApiDocs";

function Protected({ children }: { children: React.ReactNode }) {
  const { authenticated } = useAuth();
  const location = useLocation();
  if (authenticated === null) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        Loading...
      </div>
    );
  }
  if (!authenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

function ProtectedShell({ children }: { children: React.ReactNode }) {
  return (
    <Protected>
      <SelectionProvider>
        <LayerStoreProvider>
          <UndoProvider>
            <AppShell>{children}</AppShell>
          </UndoProvider>
        </LayerStoreProvider>
      </SelectionProvider>
    </Protected>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Routes>
          <Route path="/login" element={<Login />} />

          {/* Operate */}
          <Route
            path="/"
            element={
              <ProtectedShell>
                <NowPlaying />
              </ProtectedShell>
            }
          />
          <Route
            path="/lights"
            element={
              <ProtectedShell>
                <LightsOperate />
              </ProtectedShell>
            }
          />
          <Route
            path="/quick-fx"
            element={
              <ProtectedShell>
                <QuickFx />
              </ProtectedShell>
            }
          />
          <Route
            path="/scenes"
            element={
              <ProtectedShell>
                <ScenesOperate />
              </ProtectedShell>
            }
          />
          <Route
            path="/me"
            element={
              <ProtectedShell>
                <Me />
              </ProtectedShell>
            }
          />

          {/* Author */}
          <Route
            path="/author/effects"
            element={
              <ProtectedShell>
                <EffectsComposer />
              </ProtectedShell>
            }
          />
          <Route
            path="/author/palettes"
            element={
              <ProtectedShell>
                <Palettes />
              </ProtectedShell>
            }
          />
          <Route
            path="/author/scenes"
            element={
              <ProtectedShell>
                <SceneComposer />
              </ProtectedShell>
            }
          />
          <Route
            path="/author/designer"
            element={
              <ProtectedShell>
                <Designer />
              </ProtectedShell>
            }
          />

          {/* Configure */}
          <Route
            path="/config/controllers"
            element={
              <ProtectedShell>
                <Controllers />
              </ProtectedShell>
            }
          />
          <Route
            path="/config/controllers/:id"
            element={
              <ProtectedShell>
                <ControllerDetail />
              </ProtectedShell>
            }
          />
          <Route
            path="/config/models"
            element={
              <ProtectedShell>
                <Models />
              </ProtectedShell>
            }
          />
          <Route
            path="/config/models/new"
            element={
              <ProtectedShell>
                <ModelEditor />
              </ProtectedShell>
            }
          />
          <Route
            path="/config/models/:id/edit"
            element={
              <ProtectedShell>
                <ModelEditor />
              </ProtectedShell>
            }
          />

          <Route
            path="/api-docs"
            element={
              <ProtectedShell>
                <ApiDocs />
              </ProtectedShell>
            }
          />

          {/* Legacy redirects keep old links and bookmarks alive. */}
          <Route path="/effects" element={<Navigate to="/author/effects" replace />} />
          <Route path="/palettes" element={<Navigate to="/author/palettes" replace />} />
          <Route path="/designer" element={<Navigate to="/author/designer" replace />} />
          <Route path="/controllers" element={<Navigate to="/config/controllers" replace />} />
          <Route path="/controllers/:id" element={<RedirectControllerLegacy />} />
          <Route path="/models" element={<Navigate to="/config/models" replace />} />
          <Route path="/models/new" element={<Navigate to="/config/models/new" replace />} />
          <Route path="/models/:id/edit" element={<RedirectModelLegacy />} />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ToastProvider>
    </AuthProvider>
  );
}

function RedirectControllerLegacy() {
  const { pathname } = useLocation();
  const id = pathname.split("/").pop();
  return <Navigate to={`/config/controllers/${id}`} replace />;
}

function RedirectModelLegacy() {
  const { pathname } = useLocation();
  const id = pathname.split("/")[2];
  return <Navigate to={`/config/models/${id}/edit`} replace />;
}
