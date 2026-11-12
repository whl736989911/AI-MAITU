import { loader } from "@monaco-editor/react";
import { publicUrl } from "../utils/publicUrl";

// Keep Monaco and its workers on the same origin and under the configured
// dashboard base path (including non-root installations).
loader.config({
  paths: {
    vs: publicUrl("monaco/vs"),
  },
});

export { default } from "@monaco-editor/react";
