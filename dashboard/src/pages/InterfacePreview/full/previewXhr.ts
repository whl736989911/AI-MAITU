/** Route upload/progress consumers through the same local fixture fetch as the demo. */
export function installPreviewXhr(
  shouldIntercept: (url: URL) => boolean,
): void {
  const NativeXhr = window.XMLHttpRequest;
  class PreviewXhr extends NativeXhr {
    private localUrl: URL | null = null;
    private localMethod = "GET";
    private localHeaders = new Headers();
    private responseHeaders = new Headers();
    private controller: AbortController | null = null;
    private localResponse: Record<string, unknown> = {
      readyState: 0,
      status: 0,
      statusText: "",
      response: "",
      responseText: "",
      responseURL: "",
    };

    constructor() {
      super();
      for (const name of Object.keys(this.localResponse)) {
        Object.defineProperty(this, name, {
          configurable: true,
          get: () =>
            this.localUrl
              ? this.localResponse[name]
              : Reflect.get(NativeXhr.prototype, name, this),
        });
      }
    }

    override open(
      method: string,
      url: string | URL,
      async = true,
      username?: string | null,
      password?: string | null,
    ): void {
      const parsed = new URL(String(url), window.location.href);
      if (!shouldIntercept(parsed)) {
        this.localUrl = null;
        super.open(method, url, async, username, password);
        return;
      }
      this.localUrl = parsed;
      this.localMethod = method;
      this.localHeaders = new Headers();
      this.responseHeaders = new Headers();
      this.localResponse = {
        readyState: 1,
        status: 0,
        statusText: "",
        response: "",
        responseText: "",
        responseURL: parsed.href,
      };
      this.dispatchEvent(new Event("readystatechange"));
    }

    override setRequestHeader(name: string, value: string): void {
      if (!this.localUrl) return super.setRequestHeader(name, value);
      this.localHeaders.append(name, value);
    }

    override getResponseHeader(name: string): string | null {
      return this.localUrl
        ? this.responseHeaders.get(name)
        : super.getResponseHeader(name);
    }

    override getAllResponseHeaders(): string {
      return this.localUrl
        ? [...this.responseHeaders]
            .map(([name, value]) => `${name}: ${value}\r\n`)
            .join("")
        : super.getAllResponseHeaders();
    }

    override abort(): void {
      if (!this.localUrl) return super.abort();
      this.controller?.abort();
      this.localResponse.readyState = 0;
      this.dispatchEvent(new Event("abort"));
      this.dispatchEvent(new Event("loadend"));
    }

    override send(body?: Document | XMLHttpRequestBodyInit | null): void {
      if (!this.localUrl) return super.send(body);
      const controller = new AbortController();
      this.controller = controller;
      const url = this.localUrl;
      const method = this.localMethod;
      const headers = this.localHeaders;
      const total =
        body instanceof FormData
          ? [...body.values()].reduce(
              (size, value) => size + (value instanceof Blob ? value.size : 0),
              0,
            )
          : body instanceof Blob
          ? body.size
          : 0;
      this.dispatchEvent(new Event("loadstart"));
      this.upload.dispatchEvent(
        new ProgressEvent("progress", {
          lengthComputable: total > 0,
          loaded: 0,
          total,
        }),
      );
      // Give the real upload component a frame to display its pending state.
      requestAnimationFrame(() => {
        void (async () => {
          if (controller.signal.aborted) return;
          const response = await window.fetch(url, {
            method,
            headers,
            signal: controller.signal,
            body: body as BodyInit | null | undefined,
          });
          if (controller.signal.aborted) return;
          this.responseHeaders = response.headers;
          this.localResponse.status = response.status;
          this.localResponse.statusText = response.statusText;
          this.localResponse.readyState = 2;
          this.dispatchEvent(new Event("readystatechange"));
          const text = await response.text();
          if (controller.signal.aborted) return;
          this.localResponse.responseText = text;
          this.localResponse.response = text;
          this.localResponse.readyState = 4;
          this.upload.dispatchEvent(
            new ProgressEvent("progress", {
              lengthComputable: total > 0,
              loaded: total,
              total,
            }),
          );
          this.upload.dispatchEvent(new Event("load"));
          this.upload.dispatchEvent(new Event("loadend"));
          this.dispatchEvent(new Event("readystatechange"));
          this.dispatchEvent(new Event("load"));
          this.dispatchEvent(new Event("loadend"));
        })().catch(() => {
          if (controller.signal.aborted) return;
          this.localResponse.readyState = 4;
          this.localResponse.status = 0;
          this.dispatchEvent(new Event("error"));
          this.dispatchEvent(new Event("loadend"));
        });
      });
    }
  }
  window.XMLHttpRequest = PreviewXhr;
}
