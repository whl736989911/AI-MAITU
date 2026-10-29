import { createElement, type ComponentType, type CSSProperties } from "react";
import * as jsxRuntime from "react/jsx-runtime";
import type {
  OctopPluginUIHost,
  ToolRenderProps,
  ToolRendererRegistration,
} from "../../../../plugins/toolRenderers/types";
import { registerToolRenderer } from "../../../../plugins/toolRenderers/registry";
import airQuality from "../../../../../../src/octop/infra/agents/plugins/bundled/air-quality/ui/index.js?raw";
import bilibiliAnime from "../../../../../../src/octop/infra/agents/plugins/bundled/bilibili-anime/ui/index.js?raw";
import colorConvert from "../../../../../../src/octop/infra/agents/plugins/bundled/color-convert/ui/index.js?raw";
import dailyEnglish from "../../../../../../src/octop/infra/agents/plugins/bundled/daily-english/ui/index.js?raw";
import fortune from "../../../../../../src/octop/infra/agents/plugins/bundled/fortune/ui/index.js?raw";
import funFacts from "../../../../../../src/octop/infra/agents/plugins/bundled/fun-facts/ui/index.js?raw";
import githubTrending from "../../../../../../src/octop/infra/agents/plugins/bundled/github-trending/ui/index.js?raw";
import hotTopics from "../../../../../../src/octop/infra/agents/plugins/bundled/hot-topics/ui/index.js?raw";
import ipLookup from "../../../../../../src/octop/infra/agents/plugins/bundled/ip-lookup/ui/index.js?raw";
import marketQuotes from "../../../../../../src/octop/infra/agents/plugins/bundled/market-quotes/ui/index.js?raw";
import memeMaker from "../../../../../../src/octop/infra/agents/plugins/bundled/meme-maker/ui/index.js?raw";
import miniGames from "../../../../../../src/octop/infra/agents/plugins/bundled/mini-games/ui/index.js?raw";
import movieSearch from "../../../../../../src/octop/infra/agents/plugins/bundled/movie-search/ui/index.js?raw";
import parcelTracker from "../../../../../../src/octop/infra/agents/plugins/bundled/parcel-tracker/ui/index.js?raw";
import pomodoro from "../../../../../../src/octop/infra/agents/plugins/bundled/pomodoro/ui/index.js?raw";
import qrcode from "../../../../../../src/octop/infra/agents/plugins/bundled/qrcode/ui/index.js?raw";
import serverStatus from "../../../../../../src/octop/infra/agents/plugins/bundled/server-status/ui/index.js?raw";
import shortLink from "../../../../../../src/octop/infra/agents/plugins/bundled/short-link/ui/index.js?raw";
import slackCalendar from "../../../../../../src/octop/infra/agents/plugins/bundled/slack-calendar/ui/index.js?raw";
import sportsScores from "../../../../../../src/octop/infra/agents/plugins/bundled/sports-scores/ui/index.js?raw";
import tetris from "../../../../../../src/octop/infra/agents/plugins/bundled/tetris/ui/index.js?raw";
import travelInspire from "../../../../../../src/octop/infra/agents/plugins/bundled/travel-inspire/ui/index.js?raw";
import unitConvert from "../../../../../../src/octop/infra/agents/plugins/bundled/unit-convert/ui/index.js?raw";
import weather from "../../../../../../src/octop/infra/agents/plugins/bundled/weather/ui/index.js?raw";
import whatToEat from "../../../../../../src/octop/infra/agents/plugins/bundled/what-to-eat/ui/index.js?raw";
import wikiSummary from "../../../../../../src/octop/infra/agents/plugins/bundled/wiki-summary/ui/index.js?raw";
const stylePreviewJsx: typeof jsxRuntime.jsx = (type, props, key) => {
  if (type !== "iframe") return jsxRuntime.jsx(type, props, key);
  const frame = props as { style?: CSSProperties };
  return jsxRuntime.jsx(
    "div",
    {
      style: {
        ...frame.style,
        display: "grid",
        placeItems: "center",
        background: "#f2f3f5",
        color: "#667085",
      },
      "aria-hidden": true,
      children: "▶",
    },
    key,
  );
};

type RendererModule = {
  setup?: (host: OctopPluginUIHost) => void;
  default?: { setup?: (host: OctopPluginUIHost) => void };
};

type PreviewHostCallbacks = {
  onPatchResult: (callId: string, nextData: unknown) => void;
  onRequestBlocked: (path: string) => void;
  onOpenSidePanel: (options: {
    callId: string;
    title?: string;
    toolName?: string;
  }) => void;
  onCloseSidePanel: (callId: string) => void;
};

const bundledSources = [
  ["air-quality", airQuality],
  ["bilibili-anime", bilibiliAnime],
  ["color-convert", colorConvert],
  ["daily-english", dailyEnglish],
  ["fortune", fortune],
  ["fun-facts", funFacts],
  ["github-trending", githubTrending],
  ["hot-topics", hotTopics],
  ["ip-lookup", ipLookup],
  ["market-quotes", marketQuotes],
  ["meme-maker", memeMaker],
  ["mini-games", miniGames],
  ["movie-search", movieSearch],
  ["parcel-tracker", parcelTracker],
  ["pomodoro", pomodoro],
  ["qrcode", qrcode],
  ["server-status", serverStatus],
  ["short-link", shortLink],
  ["slack-calendar", slackCalendar],
  ["sports-scores", sportsScores],
  ["tetris", tetris],
  ["travel-inspire", travelInspire],
  ["unit-convert", unitConvert],
  ["weather", weather],
  ["what-to-eat", whatToEat],
  ["wiki-summary", wikiSummary],
] as const;

let activeHost: OctopPluginUIHost | null = null;
let setupComplete = false;
let setupPending: Promise<void> | null = null;

export async function registerBundledToolRenderers(
  callbacks: PreviewHostCallbacks,
  locale: string,
): Promise<void> {
  activeHost = {
    registerRenderer() {
      throw new Error(
        "Preview host registration is available only during bundled UI setup.",
      );
    },
    getToolContext: () => ({
      agentId: null,
      threadId: null,
      locale,
      theme: "light",
    }),
    patchResult: (callId, nextData) =>
      callbacks.onPatchResult(callId, nextData),
    request: async <T>(path: string): Promise<T> => {
      callbacks.onRequestBlocked(path);
      throw new Error(
        "External plugin requests are disabled in the local chat preview.",
      );
    },
    openSidePanel: (options) => callbacks.onOpenSidePanel(options),
    closeSidePanel: (callId) => callbacks.onCloseSidePanel(callId),
  };
  if (setupComplete) return;
  if (setupPending) return setupPending;
  setupPending = (async () => {
    for (const [pluginId, source] of bundledSources) {
      const objectUrl = URL.createObjectURL(
        new Blob([source], { type: "text/javascript" }),
      );
      const globals = window as unknown as { __OCTOP_JSX__: typeof jsxRuntime };
      const previousRuntime = globals.__OCTOP_JSX__;
      if (pluginId === "bilibili-anime") {
        globals.__OCTOP_JSX__ = {
          ...jsxRuntime,
          jsx: stylePreviewJsx,
          jsxs: stylePreviewJsx,
        };
      }
      try {
        // These fixed build-time assets require the React globals installed before setup; the Blob URL is never fetched from a plugin endpoint.
        const imported = (await import(
          /* @vite-ignore */ objectUrl
        )) as RendererModule;
        const setup = imported.setup ?? imported.default?.setup;
        if (!setup)
          throw new Error(`Bundled UI ${pluginId} has no setup(host) export.`);
        setup({
          registerRenderer(registration) {
            const original =
              registration.component as ComponentType<ToolRenderProps>;
            const wrapped: ComponentType<ToolRenderProps> = (props) => {
              if (!activeHost) return null;
              return createElement(original, {
                ...props,
                host: activeHost,
              });
            };
            const record: ToolRendererRegistration = {
              ...registration,
              pluginId,
              component: wrapped,
            };
            registerToolRenderer(record);
          },
          getToolContext: () => ({
            agentId: null,
            threadId: null,
            locale,
            theme: "light",
          }),
          patchResult: (callId, nextData) =>
            callbacks.onPatchResult(callId, nextData),
          request: async <T>(path: string): Promise<T> => {
            callbacks.onRequestBlocked(path);
            throw new Error(
              "External plugin requests are disabled in the local chat preview.",
            );
          },
          openSidePanel: (options) => callbacks.onOpenSidePanel(options),
          closeSidePanel: (callId) => callbacks.onCloseSidePanel(callId),
        });
      } finally {
        globals.__OCTOP_JSX__ = previousRuntime;
        URL.revokeObjectURL(objectUrl);
      }
    }
    setupComplete = true;
  })();
  return setupPending;
}
