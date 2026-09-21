/**
 * StorageBackendDrawer.test.tsx — the docker save path must not rebuild the
 * stored config document from the form-owned fields alone.
 *
 * Regression test for the "silent config truncation" bug: saving a docker
 * backend re-parsed the advanced textarea and rebuilt `config_json` from an
 * empty object, so every key the form does not own — the harness passthrough
 * keys `allow_network` / `memory` / `cpus` / `volumes` / `environment` /
 * `previewable` / … — was dropped without a word, and an unparseable document
 * was replaced by `{}` just as silently. `previewable` is the observable one:
 * once it is gone the storage browse button greys out.
 *
 * Contract under test:
 *   - re-saving an untouched docker backend keeps the keys the form does not own
 *   - the sandbox keys the form owns still win, stale scope keys are still pruned
 *   - an unparseable advanced document is refused with an inline field error
 *     and nothing is sent
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../../api/request", () => ({
  request: vi.fn(),
}));

vi.mock("@/utils/antdMessage", () => ({
  message: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    loading: vi.fn(() => vi.fn()),
  },
}));

import { request } from "../../../api/request";
import { StorageBackendDrawer } from "./StorageBackendModal";
import type { StorageBackendRow } from "./useStorageBackends";

const api = vi.mocked(request, true);

const DOCKER_ROW: StorageBackendRow = {
  id: 1,
  name: "docker-demo",
  kind: "docker",
  endpoint: null,
  access_key: null,
  bucket: "python:3.12-slim",
  region: null,
  config_json: JSON.stringify({
    previewable: true,
    memory: "256m",
    sandbox_id: "stale_box",
  }),
  note: null,
  enabled: true,
  created_at: 0,
  updated_at: 0,
};

/** The PATCH the drawer sent, decoded down to its ``config_json`` document. */
function patchedConfig(): Record<string, unknown> {
  const patch = api.mock.calls.find(([, init]) => init?.method === "PATCH");
  if (!patch) throw new Error("no PATCH was sent");
  const body = JSON.parse(String(patch[1]?.body)) as { config_json: string };
  return JSON.parse(body.config_json) as Record<string, unknown>;
}

function renderDrawer(editing: StorageBackendRow = DOCKER_ROW) {
  return render(
    <StorageBackendDrawer
      open
      editing={editing}
      onClose={vi.fn()}
      onSaved={vi.fn()}
    />,
  );
}

async function save(): Promise<void> {
  await userEvent.click(screen.getByRole("button", { name: "common.save" }));
}

beforeEach(() => {
  vi.clearAllMocks();
  api.mockResolvedValue({ id: 1, ok: false });
});

describe("<StorageBackendDrawer /> docker config", () => {
  it("keeps config keys the form does not own when re-saving", async () => {
    renderDrawer();
    await save();

    await waitFor(() => expect(patchedConfig()).toBeTruthy());
    expect(patchedConfig()).toEqual({
      // untouched foreign keys survive the save …
      previewable: true,
      memory: "256m",
      // … the sandbox keys the form owns are written …
      sandbox_scope: "agent",
      sandbox_prefix: "octop_sandbox",
      // … and a sandbox_id left over from another scope is still pruned.
    });
  });

  it("refuses an unparseable advanced document instead of saving an empty one", async () => {
    renderDrawer();
    await userEvent.click(await screen.findByText("storage.advancedConfig"));

    const textarea = await screen.findByPlaceholderText('{"path_style": true}');
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "{{not valid json");
    await save();

    expect(
      api.mock.calls.some(([, init]) => init?.method === "PATCH"),
    ).toBe(false);
    expect(
      await screen.findByText("storage.invalidConfigJson"),
    ).toBeInTheDocument();
  });
});
