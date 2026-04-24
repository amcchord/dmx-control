import React from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { ToastProvider } from "./toast";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Controllers from "./pages/Controllers";
import ControllerDetail from "./pages/ControllerDetail";
import Models from "./pages/Models";
import ModelEditor from "./pages/ModelEditor";
import Palettes from "./pages/Palettes";
import Scenes from "./pages/Scenes";
import Designer from "./pages/Designer";
import ApiDocs from "./pages/ApiDocs";
import Nav from "./components/Nav";

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

function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-full flex-col">
      <Nav />
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 pb-20 pt-6 sm:px-6 lg:px-8">
        {children}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/"
            element={
              <Protected>
                <Layout>
                  <Dashboard />
                </Layout>
              </Protected>
            }
          />
          <Route
            path="/controllers"
            element={
              <Protected>
                <Layout>
                  <Controllers />
                </Layout>
              </Protected>
            }
          />
          <Route
            path="/controllers/:id"
            element={
              <Protected>
                <Layout>
                  <ControllerDetail />
                </Layout>
              </Protected>
            }
          />
          <Route
            path="/models"
            element={
              <Protected>
                <Layout>
                  <Models />
                </Layout>
              </Protected>
            }
          />
          <Route
            path="/models/new"
            element={
              <Protected>
                <Layout>
                  <ModelEditor />
                </Layout>
              </Protected>
            }
          />
          <Route
            path="/models/:id/edit"
            element={
              <Protected>
                <Layout>
                  <ModelEditor />
                </Layout>
              </Protected>
            }
          />
          <Route
            path="/palettes"
            element={
              <Protected>
                <Layout>
                  <Palettes />
                </Layout>
              </Protected>
            }
          />
          <Route
            path="/scenes"
            element={
              <Protected>
                <Layout>
                  <Scenes />
                </Layout>
              </Protected>
            }
          />
          <Route
            path="/designer"
            element={
              <Protected>
                <Layout>
                  <Designer />
                </Layout>
              </Protected>
            }
          />
          <Route
            path="/api-docs"
            element={
              <Protected>
                <Layout>
                  <ApiDocs />
                </Layout>
              </Protected>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ToastProvider>
    </AuthProvider>
  );
}
