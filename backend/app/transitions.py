"""Cancellable color fades. Later live actions always take precedence."""
import threading
import time
from .artnet import manager

_generation = 0
_lock = threading.Lock()


def cancel() -> int:
    global _generation
    with _lock:
        _generation += 1
        return _generation


def fade(targets: dict[int, dict], duration: float, starts: dict[int, dict]) -> None:
    generation = cancel()
    if duration <= 0:
        return
    def run():
        begin = time.monotonic()
        while True:
            with _lock:
                if generation != _generation:
                    return
                alpha = min(1.0, (time.monotonic() - begin) / duration)
                for lid, target in targets.items():
                    start = starts.get(lid, target)
                    frame = dict(target)
                    # Fade color and intensity only. Motion and other device
                    # controls remain at the destination's configured values.
                    for role in ("r", "g", "b", "w", "a", "uv", "dimmer"):
                        a, b = int(start.get(role, 0)), int(target.get(role, 0))
                        if role == "dimmer":
                            a = a if start.get("on", True) else 0
                            b = b if target.get("on", True) else 0
                        frame[role] = round(a + (b - a) * alpha)
                    frame["on"] = target.get("on", True) if alpha == 1 else True
                    manager.set_light_state_deferred(lid, frame)
                manager.flush_dirty()
            if alpha == 1:
                return
            time.sleep(1 / 30)
    threading.Thread(target=run, name="scene-fade", daemon=True).start()
