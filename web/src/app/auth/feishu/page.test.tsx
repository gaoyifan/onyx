import React from "react";
import { render, screen, waitFor } from "@tests/setup/test-utils";
import FeishuAuthPage from "./page";

describe("FeishuAuthPage", () => {
  let fetchSpy: jest.SpyInstance;
  let appendChildSpy: jest.SpyInstance;

  beforeEach(() => {
    fetchSpy = jest.spyOn(global, "fetch");
    appendChildSpy = jest.spyOn(document.head, "appendChild");
    Object.defineProperty(window, "h5sdk", {
      configurable: true,
      value: {
        ready: (callback: () => void) => callback(),
        error: jest.fn(),
      },
    });
    Object.defineProperty(window, "tt", {
      configurable: true,
      value: {
        requestAccess: jest.fn(({ success }) =>
          success({ code: "feishu-code" })
        ),
      },
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    appendChildSpy.mockRestore();
    document.head
      .querySelectorAll("script")
      .forEach((script) => script.remove());
  });

  test("submits Feishu auth code and state to backend", async () => {
    window.history.pushState(null, "", "/auth/feishu?from=p2p_card_button");
    appendChildSpy.mockImplementation((node: Node) => {
      setTimeout(() => {
        const script = node as HTMLScriptElement;
        script.onload?.(new Event("load"));
      });
      return node;
    });
    fetchSpy
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          enabled: true,
          app_id: "cli_test",
          state: "state-token",
          sdk_url: "https://example.com/h5.js",
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ redirect_url: "/app" }),
      } as Response);

    render(<FeishuAuthPage />);

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/auth/feishu/login",
        expect.objectContaining({
          method: "POST",
          credentials: "include",
          body: JSON.stringify({ code: "feishu-code", state: "state-token" }),
        })
      );
    });
    expect(window.location.pathname).toBe("/auth/feishu");
    expect(window.location.search).toBe("");
  });

  test("shows error when Feishu SDK cannot load", async () => {
    appendChildSpy.mockImplementation((node: Node) => {
      setTimeout(() => {
        const script = node as HTMLScriptElement;
        script.onerror?.(new Event("error"));
      });
      return node;
    });
    fetchSpy.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        enabled: true,
        app_id: "cli_test",
        state: "state-token",
        sdk_url: "https://example.com/h5.js",
      }),
    } as Response);

    render(<FeishuAuthPage />);

    expect(
      await screen.findByText("Unable to load Feishu SDK.")
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /back to login/i })
    ).toBeInTheDocument();
  });

  test("falls back to requestAuthCode when requestAccess is unsupported", async () => {
    appendChildSpy.mockImplementation((node: Node) => {
      setTimeout(() => {
        const script = node as HTMLScriptElement;
        script.onload?.(new Event("load"));
      });
      return node;
    });
    Object.defineProperty(window, "tt", {
      configurable: true,
      value: {
        requestAccess: jest.fn(({ fail }) => fail({ errno: 103 })),
        requestAuthCode: jest.fn(({ success }) =>
          success({ code: "legacy-feishu-code" })
        ),
      },
    });
    fetchSpy
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          enabled: true,
          app_id: "cli_test",
          state: "state-token",
          sdk_url: "https://example.com/h5.js",
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ redirect_url: "/app" }),
      } as Response);

    render(<FeishuAuthPage />);

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/auth/feishu/login",
        expect.objectContaining({
          method: "POST",
          credentials: "include",
          body: JSON.stringify({
            code: "legacy-feishu-code",
            state: "state-token",
          }),
        })
      );
    });
  });

  test("shows error when backend rejects login", async () => {
    appendChildSpy.mockImplementation((node: Node) => {
      setTimeout(() => {
        const script = node as HTMLScriptElement;
        script.onload?.(new Event("load"));
      });
      return node;
    });
    fetchSpy
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          enabled: true,
          app_id: "cli_test",
          state: "state-token",
          sdk_url: "https://example.com/h5.js",
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: false,
        json: async () => ({ detail: "Missing Feishu user id." }),
      } as Response);

    render(<FeishuAuthPage />);

    expect(
      await screen.findByText("Missing Feishu user id.")
    ).toBeInTheDocument();
  });
});
