import fs from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import type { Plugin } from "vite";

const MONACO_VS_PREFIX = "/monaco/vs";

function monacoVsSourceDir(rootDir: string): string {
  return path.resolve(rootDir, "node_modules/monaco-editor/min/vs");
}

function monacoContentType(file: string): string {
  switch (path.extname(file)) {
    case ".css":
      return "text/css; charset=utf-8";
    case ".ttf":
      return "font/ttf";
    case ".woff":
      return "font/woff";
    case ".woff2":
      return "font/woff2";
    case ".svg":
      return "image/svg+xml";
    case ".json":
      return "application/json; charset=utf-8";
    default:
      return "text/javascript; charset=utf-8";
  }
}

function resolveMonacoFile(
  vsDir: string,
  url: string | undefined,
): string | null {
  let pathname: string;
  try {
    pathname = decodeURIComponent((url ?? "/").split("?")[0] ?? "/");
  } catch {
    return null;
  }
  const rel = pathname.replace(/^\/+/, "");
  if (!rel || rel.includes("\0")) return null;
  const root = path.resolve(vsDir);
  const file = path.resolve(root, rel);
  if (file !== root && !file.startsWith(root + path.sep)) return null;
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return null;
  return file;
}

function sendMonacoFile(
  vsDir: string,
  req: IncomingMessage,
  res: ServerResponse,
): boolean {
  const file = resolveMonacoFile(vsDir, req.url);
  if (!file) return false;
  res.statusCode = 200;
  res.setHeader("Content-Type", monacoContentType(file));
  res.setHeader("Cache-Control", "no-cache");
  fs.createReadStream(file).pipe(res);
  return true;
}

/** Serve and package Monaco's complete AMD module/worker tree locally. */
export function monacoSameOrigin(rootDir: string): Plugin {
  return {
    name: "octop-monaco-vs",
    configureServer(server) {
      const vsDir = monacoVsSourceDir(rootDir);
      server.middlewares.use(MONACO_VS_PREFIX, (req, res) => {
        if (sendMonacoFile(vsDir, req, res)) return;
        res.statusCode = 404;
        res.end("monaco asset not found");
      });
    },
    writeBundle(options) {
      const vsDir = monacoVsSourceDir(rootDir);
      if (!fs.existsSync(vsDir)) {
        this.error(
          `Monaco assets missing at ${vsDir}. Install the monaco-editor dependency.`,
        );
      }
      if (!options.dir) return;
      fs.cpSync(vsDir, path.join(options.dir, "monaco", "vs"), {
        recursive: true,
      });
    },
  };
}
