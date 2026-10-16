import { useEffect, useState } from "react";
import { Api, Controller } from "../api";
export function useControllers() {
  const [controllers, setControllers] = useState<Controller[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    const refresh = () =>
      Api.listControllers()
        .then((c) => {
          if (alive) {
            setControllers(c);
            setError("");
          }
        })
        .catch(() => {
          if (alive)
            setError("Controller status unavailable. Check your connection.");
        });
    void refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 5000);
    window.addEventListener("dmx:changed", refresh);
    return () => {
      alive = false;
      clearInterval(timer);
      window.removeEventListener("dmx:changed", refresh);
    };
  }, []);
  return { controllers, error };
}
