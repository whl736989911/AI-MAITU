import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  token: { value: "" },
  registration: { value: null as unknown },
  notification: { open: vi.fn(), destroy: vi.fn() },
}));

class FakeSocket {
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readonly url: string;
  readyState = 0;
  closeCount = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }

  close() {
    this.closeCount += 1;
    this.onclose?.();
  }

  send() {}
}

class FakeSystemNotification {
  static permission = "granted";
  readonly tag: string;
  readonly data: unknown;
  closed = false;
  onclose: (() => void) | null = null;
  onclick: (() => void) | null = null;

  constructor(_title: string, options: NotificationOptions = {}) {
    this.tag = options.tag ?? "";
    this.data = options.data;
  }

  close() {
    this.closed = true;
    this.onclose?.();
  }
}

function makeRegistration() {
  const notifications: FakeSystemNotification[] = [];
  const addNotification = (_title: string, options: NotificationOptions) => {
    notifications.push(new FakeSystemNotification("", options));
  };
  const registration = {
    notifications,
    addNotification,
    getNotifications: vi.fn(async (filter?: GetNotificationOptions) =>
      notifications.filter(
        (notice) =>
          !notice.closed && (!filter?.tag || notice.tag === filter.tag),
      ),
    ),
    showNotification: vi.fn(
      async (title: string, options: NotificationOptions) => {
        addNotification(title, options);
      },
    ),
  };
  return registration;
}

vi.mock("antd", () => ({
  App: { useApp: () => ({ notification: mocks.notification }) },
}));
vi.mock("../api/modules/wsNotifications", () => ({
  buildDashboardNotifyWsUrl: () =>
    `ws://dashboard/notifications?token=${mocks.token.value}`,
}));
vi.mock("../api/request", () => ({
  AUTH_IDENTITY_CHANGED_EVENT: "octop:auth-identity-changed",
  getAuthToken: () => mocks.token.value,
}));
vi.mock("../context/AgentContext", () => ({
  useAgent: () => ({ refresh: vi.fn(), agents: [] }),
}));
vi.mock("../pages/Chat/hooks/chatStore", () => ({
  emitSessionEvent: vi.fn(),
  invalidateHistory: vi.fn(),
}));
vi.mock("../pages/Experts/components/iconForName", () => ({
  ExpertIcon: () => null,
}));
vi.mock("../sw-register", () => ({
  peekSystemNotifyRegistration: () => mocks.registration.value,
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));

import { AUTH_IDENTITY_CHANGED_EVENT } from "../api/request";
import { useDashboardPushToast } from "./useDashboardPushToast";

function Harness() {
  useDashboardPushToast();
  return null;
}

function push(socket: FakeSocket, threadId: string) {
  act(() => {
    socket.onmessage?.({
      data: JSON.stringify({
        type: "dashboard_push",
        agent_id: "agent",
        thread_id: threadId,
        text: `${threadId} reminder`,
      }),
    } as MessageEvent);
  });
}

function changeIdentity(token: string) {
  mocks.token.value = token;
  act(() => {
    window.dispatchEvent(new Event(AUTH_IDENTITY_CHANGED_EVENT));
  });
}

const originalSecureContext = window.isSecureContext;
const originalVisibilityState = document.visibilityState;
const originalServiceWorker = Object.getOwnPropertyDescriptor(
  navigator,
  "serviceWorker",
);

beforeEach(() => {
  FakeSocket.instances = [];
  mocks.token.value = "user-a-token";
  mocks.registration.value = null;
  mocks.notification.open.mockReset();
  mocks.notification.destroy.mockReset();
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("Notification", FakeSystemNotification);
  Object.defineProperty(window, "isSecureContext", {
    configurable: true,
    value: true,
  });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "hidden",
  });
  delete (navigator as Navigator & { serviceWorker?: ServiceWorkerContainer })
    .serviceWorker;
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  Object.defineProperty(window, "isSecureContext", {
    configurable: true,
    value: originalSecureContext,
  });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: originalVisibilityState,
  });
  if (originalServiceWorker) {
    Object.defineProperty(navigator, "serviceWorker", originalServiceWorker);
  } else {
    delete (navigator as Navigator & { serviceWorker?: ServiceWorkerContainer })
      .serviceWorker;
  }
});

describe("useDashboardPushToast account changes", () => {
  it("closes the previous user's socket and ignores its late frames", () => {
    render(<Harness />);
    const previous = FakeSocket.instances[0]!;

    changeIdentity("user-b-token");

    expect(previous.closeCount).toBe(1);
    expect(FakeSocket.instances).toHaveLength(2);
    expect(FakeSocket.instances[1]!.url).toContain("user-b-token");

    push(previous, "a-thread");
    expect(mocks.notification.open).not.toHaveBeenCalled();
  });

  it("only closes A notifications when a deferred notification query returns after B appears", async () => {
    const registration = makeRegistration();
    mocks.registration.value = registration;
    let resolveQuery!: (notices: FakeSystemNotification[]) => void;
    render(<Harness />);
    push(FakeSocket.instances[0]!, "a-thread");
    const aNotice = registration.notifications[0]!;

    const query = Promise.withResolvers<FakeSystemNotification[]>();
    registration.getNotifications.mockImplementationOnce(() => query.promise);
    resolveQuery = (notices) => query.resolve(notices);
    changeIdentity("user-b-token");
    push(FakeSocket.instances[1]!, "b-thread");
    const bNotice = registration.notifications[1]!;

    await act(async () => {
      resolveQuery([...registration.notifications]);
      await Promise.resolve();
    });

    expect(aNotice.closed).toBe(true);
    expect(bNotice.closed).toBe(false);
  });

  it("closes A's late system notification without closing B's", async () => {
    const registration = makeRegistration();
    mocks.registration.value = registration;
    let finishA!: () => void;
    const show = Promise.withResolvers<void>();
    registration.showNotification.mockImplementationOnce((title, options) => {
      finishA = () => show.resolve();
      return show.promise.then(() =>
        registration.addNotification(title, options),
      );
    });
    render(<Harness />);
    push(FakeSocket.instances[0]!, "a-thread");

    changeIdentity("user-b-token");
    push(FakeSocket.instances[1]!, "b-thread");
    const bNotice = registration.notifications[0]!;

    await act(async () => {
      finishA();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(registration.notifications).toHaveLength(2);
    expect(registration.notifications[1]!.closed).toBe(true);
    expect(bNotice.closed).toBe(false);
  });

  it("ignores A's deferred service-worker readiness but still delivers B's push", async () => {
    const registration = makeRegistration();
    mocks.registration.value = null;
    const readyDeferred = Promise.withResolvers<ServiceWorkerRegistration>();
    let resolveReady!: (value: ServiceWorkerRegistration) => void;
    const ready = readyDeferred.promise;
    resolveReady = (value) => readyDeferred.resolve(value);
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { ready },
    });
    render(<Harness />);
    push(FakeSocket.instances[0]!, "a-thread");

    changeIdentity("user-b-token");
    push(FakeSocket.instances[1]!, "b-thread");
    await act(async () => {
      resolveReady(registration as unknown as ServiceWorkerRegistration);
      await ready;
      await Promise.resolve();
    });

    expect(registration.showNotification).toHaveBeenCalledTimes(1);
    expect(registration.notifications).toHaveLength(1);
    expect(registration.notifications[0]!.closed).toBe(false);
    expect(registration.notifications[0]!.data).toMatchObject({
      pushGeneration: 1,
    });
  });
});
