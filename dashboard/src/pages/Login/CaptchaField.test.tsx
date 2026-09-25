import { describe, expect, it, vi } from "vitest";
import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import CaptchaField, { type CaptchaFieldHandle } from "./CaptchaField";
import { loginCaptchaBody } from "./captchaAdapters";

const labels = {
  slideHint: "Slide",
  slideVerifiedLabel: "OK",
  unsupportedLabel: "Use this captcha on a supported client",
};

describe("CaptchaField", () => {
  it("renders the slider for the default provider", () => {
    render(
      <CaptchaField
        config={{ provider: "slider" }}
        {...labels}
        onReadyChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("slide-captcha-track")).toBeInTheDocument();
    expect(screen.queryByTestId("captcha-widget")).not.toBeInTheDocument();
  });

  it("renders a vendor host for checkbox providers", () => {
    render(
      <CaptchaField
        config={{ provider: "turnstile", site_key: "0xsite" }}
        {...labels}
        onReadyChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("captcha-widget")).toBeInTheDocument();
    expect(screen.queryByTestId("slide-captcha-track")).not.toBeInTheDocument();
  });

  it("shows a static error when the slug has no adapter", () => {
    render(
      <CaptchaField
        config={{ provider: "unknown-vendor", site_key: "x" }}
        {...labels}
        onReadyChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("captcha-unsupported")).toHaveTextContent(
      labels.unsupportedLabel,
    );
  });

  it("returns no token from the slider handle", async () => {
    const ref = createRef<CaptchaFieldHandle>();
    render(
      <CaptchaField
        ref={ref}
        config={{ provider: "slider" }}
        {...labels}
        onReadyChange={vi.fn()}
      />,
    );
    await expect(ref.current?.getToken()).resolves.toBeUndefined();
  });

  it("resolves ticket:randstr from the tencent popup", async () => {
    const onReady = vi.fn();
    const ref = createRef<CaptchaFieldHandle>();
    class FakeTencentCaptcha {
      constructor(
        _appId: string,
        cb: (res: { ret: number; ticket?: string; randstr?: string }) => void,
      ) {
        this.cb = cb;
      }
      cb: (res: { ret: number; ticket?: string; randstr?: string }) => void;
      show() {
        this.cb({ ret: 0, ticket: "tr03ticket", randstr: "@rand" });
      }
    }
    (window as unknown as Record<string, unknown>).TencentCaptcha =
      FakeTencentCaptcha;
    try {
      render(
        <CaptchaField
          ref={ref}
          config={{ provider: "tencent", site_key: "195642000" }}
          {...labels}
          onReadyChange={onReady}
        />,
      );
      expect(screen.getByTestId("captcha-popup")).toBeInTheDocument();
      expect(onReady).toHaveBeenCalledWith(true);
      await expect(ref.current?.getToken()).resolves.toBe("tr03ticket:@rand");
    } finally {
      delete (window as unknown as Record<string, unknown>).TencentCaptcha;
    }
  });

  it("resolves undefined when the tencent popup is closed", async () => {
    const ref = createRef<CaptchaFieldHandle>();
    class FakeTencentCaptcha {
      constructor(_appId: string, cb: (res: { ret: number }) => void) {
        this.cb = cb;
      }
      cb: (res: { ret: number }) => void;
      show() {
        this.cb({ ret: 2 });
      }
    }
    (window as unknown as Record<string, unknown>).TencentCaptcha =
      FakeTencentCaptcha;
    try {
      render(
        <CaptchaField
          ref={ref}
          config={{ provider: "tencent", site_key: "195642000" }}
          {...labels}
          onReadyChange={vi.fn()}
        />,
      );
      await expect(ref.current?.getToken()).resolves.toBeUndefined();
    } finally {
      delete (window as unknown as Record<string, unknown>).TencentCaptcha;
    }
  });
  it("submits the GeeTest v4 validation fields as a JSON token", async () => {
    const ref = createRef<CaptchaFieldHandle>();
    let onSuccess: (() => void) | undefined;
    const init = vi.fn(
      (
        options: Record<string, unknown>,
        callback: (captcha: {
          onReady: (fn: () => void) => void;
          onSuccess: (fn: () => void) => void;
          onError: (fn: () => void) => void;
          onClose: (fn: () => void) => void;
          showCaptcha: () => void;
          getValidate: () => {
            lot_number: string;
            captcha_output: string;
            pass_token: string;
            gen_time: string;
          };
        }) => void,
      ) => {
        expect(options).toMatchObject({ captchaId: "gt4-id", product: "bind" });
        callback({
          onReady: (fn) => fn(),
          onSuccess: (fn) => {
            onSuccess = fn;
          },
          onError: vi.fn(),
          onClose: vi.fn(),
          showCaptcha: () => onSuccess?.(),
          getValidate: () => ({
            lot_number: "lot-1",
            captcha_output: "out-1",
            pass_token: "pass-1",
            gen_time: "time-1",
          }),
        });
      },
    );
    (window as unknown as Record<string, unknown>).initGeetest4 = init;
    try {
      render(
        <CaptchaField
          ref={ref}
          config={{ provider: "geetest-v4", site_key: "gt4-id" }}
          {...labels}
          onReadyChange={vi.fn()}
        />,
      );
      expect(screen.getByTestId("captcha-popup")).toBeInTheDocument();
      await expect(ref.current?.getToken()).resolves.toBe(
        JSON.stringify({
          lot_number: "lot-1",
          captcha_output: "out-1",
          pass_token: "pass-1",
          gen_time: "time-1",
        }),
      );
      expect(init).toHaveBeenCalledTimes(1);
    } finally {
      delete (window as unknown as Record<string, unknown>).initGeetest4;
    }
  });
});

describe("loginCaptchaBody", () => {
  it("omits captcha_token unless a strong token is present", () => {
    expect(loginCaptchaBody("a", "b")).toEqual({
      username: "a",
      password: "b",
    });
    expect(loginCaptchaBody("a", "b", "tok")).toEqual({
      username: "a",
      password: "b",
      captcha_token: "tok",
    });
  });
});
