import { describe, expect, it } from "vitest";
import { createInstance } from "i18next";
import en from "./en.json";
import zh from "./zh.json";

/**
 * `en.json` and `zh.json` are the two shipped UI bundles; i18next falls back
 * to the key name when a lookup misses, so a key that exists in only one of
 * them silently renders as `memory.candidates.promote` (or in the wrong
 * language) in the other locale. Both bundles must therefore stay in parity.
 *
 * The backend catalog has the same guard in
 * ``tests/unit/i18n/test_catalog.py::test_en_and_zh_share_same_keys``.
 */

type Bundle = Record<string, unknown>;

/** Object values are namespaces (`memory.candidates`), everything else is a lookup target. */
function isBranch(value: unknown): value is Bundle {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Resolve a bundle into ``dotted.path -> leaf`` entries, i.e. what ``t()`` looks up. */
function flattenLeaves(
  bundle: Bundle,
  prefix = "",
  out = new Map<string, unknown>(),
): Map<string, unknown> {
  for (const [key, value] of Object.entries(bundle)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (isBranch(value)) flattenLeaves(value, path, out);
    else out.set(path, value);
  }
  return out;
}

/** Resolve a bundle into ``dotted.path -> direct child keys`` for every nested object. */
function flattenBranches(
  bundle: Bundle,
  prefix = "",
  out = new Map<string, string[]>(),
): Map<string, string[]> {
  for (const [key, value] of Object.entries(bundle)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (isBranch(value)) {
      out.set(path, Object.keys(value));
      flattenBranches(value, path, out);
    }
  }
  return out;
}

/** One side nests an object where the other side has a string, or holds an empty object. */
function collectStructureProblems(
  branches: Map<string, string[]>,
  otherLeaves: Map<string, unknown>,
  ownLabel: string,
  otherLabel: string,
): string[] {
  const problems: string[] = [];
  for (const [path, children] of branches) {
    if (otherLeaves.has(path)) {
      problems.push(
        `${ownLabel} nests an object at "${path}" while ${otherLabel} has a string there`,
      );
    }
    if (!children.length) {
      problems.push(`${ownLabel} has an empty object at "${path}"`);
    }
  }
  return problems;
}

describe("locale bundle parity (en / zh)", () => {
  it("exposes the same leaf keys on both sides", () => {
    const enLeaves = flattenLeaves(en as Bundle);
    const zhLeaves = flattenLeaves(zh as Bundle);
    expect(enLeaves.size).toBeGreaterThan(1000);
    const enOnly = [...enLeaves.keys()]
      .filter((key) => !zhLeaves.has(key))
      .sort();
    const zhOnly = [...zhLeaves.keys()]
      .filter((key) => !enLeaves.has(key))
      .sort();
    expect({ enOnly, zhOnly }).toEqual({ enOnly: [], zhOnly: [] });
  });

  it("resolves every translation in each locale, including locale-specific plurals", async () => {
    const instance = createInstance();
    await instance.init({
      fallbackLng: false,
      resources: { en: { translation: en }, zh: { translation: zh } },
    });
    const keys = new Set(
      [
        ...flattenLeaves(en as Bundle).keys(),
        ...flattenLeaves(zh as Bundle).keys(),
      ].map((key) => key.replace(/_(zero|one|two|few|many|other)$/, "")),
    );
    const missing: string[] = [];
    for (const key of keys) {
      for (const lng of ["en", "zh"]) {
        for (const count of [0, 1, 2]) {
          if (!instance.exists(key, { lng, count })) {
            missing.push(`${lng}: ${key} (count=${count})`);
          }
        }
      }
    }
    expect(missing).toEqual([]);
  });

  it("keeps object and leaf structure aligned on both sides", () => {
    const enLeaves = flattenLeaves(en as Bundle);
    const zhLeaves = flattenLeaves(zh as Bundle);
    const problems = [
      ...collectStructureProblems(
        flattenBranches(en as Bundle),
        zhLeaves,
        "en.json",
        "zh.json",
      ),
      ...collectStructureProblems(
        flattenBranches(zh as Bundle),
        enLeaves,
        "zh.json",
        "en.json",
      ),
    ];

    expect(problems.sort()).toEqual([]);
  });
});
