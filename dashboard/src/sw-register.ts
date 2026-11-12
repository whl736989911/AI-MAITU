import { publicUrl } from "./utils/publicUrl";

/**
 * Service Worker registration and lifecycle management.
 *
 * Production (all browsers including Chrome / Safari):
 *   - Register the worker and scope under Vite's configured public base.
 *   - Never auto `location.reload()` on `controllerchange` — that pattern
 *     caused full-page refresh loops (especially WebKit). Updates apply only
 *     when the user clicks the banner → `applyUpdate()`.
 *
 * Dev:
 *   - Clear any leftover production worker/cache and register notify-sw.js,
 *     which has no fetch handler, so Vite keeps serving the app normally.
 */

let pendingRegistration: ServiceWorkerRegistration | null = null;
let notifyRegistration: ServiceWorkerRegistration | null = null;
let applyingUpdate = false;

/** Active worker registration for synchronous background notification delivery. */
export function peekSystemNotifyRegistration(): ServiceWorkerRegistration | null {
  return notifyRegistration;
}

function trackNotifyRegistration(): void {
  if (!("serviceWorker" in navigator)) return;
  const ready = navigator.serviceWorker.ready;
  if (!ready) return;
  void ready.then((registration) => {
    notifyRegistration = registration;
  });
}

function notifyUpdateReady(): void {
  window.dispatchEvent(new CustomEvent("pwa:update-ready"));
}

async function unregisterAllServiceWorkers(): Promise<void> {
  const regs = await navigator.serviceWorker.getRegistrations();
  await Promise.all(regs.map((r) => r.unregister()));
  if ("caches" in window) {
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => caches.delete(k)));
  }
}

/**
 * Apply a waiting Service Worker update and reload once.
 * Only called from explicit UI actions (PwaUpdatePrompt).
 */
export async function applyUpdate(): Promise<void> {
  if (applyingUpdate) return;
  applyingUpdate = true;
  const waiting = pendingRegistration?.waiting;
  if (!waiting) {
    window.location.reload();
    return;
  }

  // Wait until the new worker takes control before reloading. Reloading
  // immediately after SKIP_WAITING can race activation, so the old worker
  // serves the page again and the update prompt reappears after restart.
  await new Promise<void>((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeoutId);
      navigator.serviceWorker.removeEventListener("controllerchange", finish);
      resolve();
    };
    const timeoutId = window.setTimeout(finish, 3000);
    navigator.serviceWorker.addEventListener("controllerchange", finish, {
      once: true,
    });
    waiting.postMessage({ type: "SKIP_WAITING" });
  });
  window.location.reload();
}

/** Production registration. Exported for unit tests (vitest always sets DEV). */
export async function registerProductionSW(): Promise<void> {
  const scope = import.meta.env.BASE_URL || "/";
  const registration = await navigator.serviceWorker.register(
    publicUrl("sw.js"),
    {
      scope,
      updateViaCache: "none",
    },
  );

  // Intentionally no controllerchange → location.reload() listener.
  // Chrome and Safari both pick up new assets via applyUpdate() instead.

  const activateWaiting = (worker: ServiceWorker) => {
    worker.postMessage({ type: "SKIP_WAITING" });
  };

  if (registration.waiting) {
    if (navigator.serviceWorker.controller) {
      pendingRegistration = registration;
      notifyUpdateReady();
    } else {
      // Uncontrolled page (shift-reload): activate so the next visit is
      // not handed back to the stale worker that caused the white screen.
      activateWaiting(registration.waiting);
    }
  }

  registration.addEventListener("updatefound", () => {
    const installing = registration.installing;
    if (!installing) return;
    installing.addEventListener("statechange", () => {
      if (installing.state !== "installed") return;
      if (navigator.serviceWorker.controller) {
        pendingRegistration = registration;
        notifyUpdateReady();
        return;
      }
      activateWaiting(installing);
    });
  });

  setInterval(
    () => {
      void registration.update();
    },
    60 * 60 * 1000,
  );
  trackNotifyRegistration();
}

async function registerNotifySW(): Promise<void> {
  const scope = import.meta.env.BASE_URL || "/";
  await navigator.serviceWorker.register(publicUrl("notify-sw.js"), {
    scope,
    updateViaCache: "none",
  });
  trackNotifyRegistration();
}

export async function registerSW(): Promise<void> {
  if (!("serviceWorker" in navigator)) return;

  if (import.meta.env.DEV) {
    try {
      await unregisterAllServiceWorkers();
    } catch (err) {
      console.warn("[SW] Dev cleanup failed:", err);
    }
    try {
      await registerNotifySW();
    } catch (err) {
      console.error("[SW] Notification worker failed:", err);
    }
    return;
  }

  try {
    await registerProductionSW();
  } catch (err) {
    console.error("[SW] Registration failed:", err);
  }
}
