"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Button, MessageCard } from "@opal/components";
import { SvgOnyxLogo } from "@opal/logos";

interface FeishuConfigResponse {
  enabled: boolean;
  app_id: string;
  state: string;
  sdk_url: string;
}

interface FeishuLoginResponse {
  redirect_url: string;
}

interface FeishuRequestAccessResult {
  code?: string;
}

interface FeishuRequestAccessOptions {
  appID: string;
  scopeList: string[];
  success: (result: FeishuRequestAccessResult) => void;
  fail: (error: unknown) => void;
}

interface FeishuRequestAuthCodeOptions {
  appId: string;
  success: (result: FeishuRequestAccessResult) => void;
  fail: (error: unknown) => void;
}

declare global {
  interface Window {
    h5sdk?: {
      ready: (callback: () => void) => void;
      error?: (callback: (error: unknown) => void) => void;
    };
    tt?: {
      requestAccess?: (options: FeishuRequestAccessOptions) => void;
      requestAuthCode?: (options: FeishuRequestAuthCodeOptions) => void;
    };
  }
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  if (typeof error === "string") {
    return error;
  }
  if (error && typeof error === "object") {
    const errorRecord = error as Record<string, unknown>;
    const errorText =
      errorRecord.errString ??
      errorRecord.errMsg ??
      errorRecord.message ??
      JSON.stringify(errorRecord);
    return `Feishu login failed: ${String(errorText)}`;
  }
  return "Feishu login failed.";
}

async function loadScript(src: string): Promise<void> {
  const existingScript = document.querySelector<HTMLScriptElement>(
    `script[src="${src}"]`
  );
  if (existingScript?.dataset.loaded === "true") {
    return;
  }

  await new Promise<void>((resolve, reject) => {
    const script = existingScript ?? document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = () => {
      script.dataset.loaded = "true";
      resolve();
    };
    script.onerror = () => reject(new Error("Unable to load Feishu SDK."));
    if (!existingScript) {
      document.head.appendChild(script);
    }
  });
}

function requestFeishuAccess(appId: string): Promise<string> {
  return new Promise((resolve, reject) => {
    if (!window.h5sdk || !window.tt) {
      reject(new Error("Please open this page inside Feishu."));
      return;
    }

    const requestAuthCode = (requestAccessError?: unknown) => {
      if (!window.tt?.requestAuthCode) {
        reject(
          requestAccessError ??
            new Error("This Feishu client does not support login.")
        );
        return;
      }

      window.tt.requestAuthCode({
        appId,
        success: (result) => {
          if (!result.code) {
            reject(new Error("Feishu did not return an auth code."));
            return;
          }
          resolve(result.code);
        },
        fail: (error) => reject(error),
      });
    };

    window.h5sdk.ready(() => {
      if (!window.tt?.requestAccess) {
        requestAuthCode();
        return;
      }

      window.tt?.requestAccess?.({
        appID: appId,
        scopeList: [],
        success: (result) => {
          if (!result.code) {
            reject(new Error("Feishu did not return an auth code."));
            return;
          }
          resolve(result.code);
        },
        fail: (error) => {
          // Some Feishu clients return 2700002/99991679 for requestAccess even
          // though the legacy login-free API can still issue an auth code.
          requestAuthCode(error);
        },
      });
    });
  });
}

export default function FeishuAuthPage() {
  const [status, setStatus] = useState("Connecting to Feishu...");
  const [error, setError] = useState<string | null>(null);

  const nextUrl = useMemo(() => {
    if (typeof window === "undefined") {
      return "/app";
    }
    return new URLSearchParams(window.location.search).get("next") ?? "/app";
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loginWithFeishu() {
      try {
        const configUrl = new URL(
          "/api/auth/feishu/config",
          window.location.origin
        );
        configUrl.searchParams.set("next", nextUrl);

        const configResponse = await fetch(configUrl.toString(), {
          credentials: "include",
        });
        if (!configResponse.ok) {
          throw new Error("Unable to load Feishu login configuration.");
        }

        const config = (await configResponse.json()) as FeishuConfigResponse;
        if (!config.enabled || !config.app_id || !config.state) {
          throw new Error("Feishu login is not enabled.");
        }

        const canonicalLoginPath = "/auth/feishu";
        if (
          window.location.pathname === canonicalLoginPath &&
          window.location.search
        ) {
          window.history.replaceState(null, "", canonicalLoginPath);
        }

        if (!cancelled) {
          setStatus("Waiting for Feishu authorization...");
        }
        await loadScript(config.sdk_url);
        const code = await requestFeishuAccess(config.app_id);

        if (!cancelled) {
          setStatus("Signing in...");
        }
        const loginResponse = await fetch("/api/auth/feishu/login", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code, state: config.state }),
        });
        if (!loginResponse.ok) {
          const body = await loginResponse.json().catch(() => ({}));
          throw new Error(
            body.detail || body.message || "Feishu login failed."
          );
        }

        const loginResult = (await loginResponse.json()) as FeishuLoginResponse;
        window.location.href = loginResult.redirect_url || "/app";
      } catch (loginError) {
        if (!cancelled) {
          setError(getErrorMessage(loginError));
          setStatus("Feishu login failed.");
        }
      }
    }

    void loginWithFeishu();

    return () => {
      cancelled = true;
    };
  }, [nextUrl]);

  return (
    <div className="p-4 flex flex-col items-center justify-center min-h-screen bg-background">
      <div className="w-full max-w-md flex items-start flex-col bg-background-tint-00 rounded-16 shadow-lg shadow-02 p-6">
        <SvgOnyxLogo size={44} className="text-theme-primary-05" />
        <div className="w-full mt-3 flex flex-col gap-4">
          {error ? (
            <MessageCard variant="error" title={error} />
          ) : (
            <MessageCard variant="info" title={status} />
          )}
          {error && (
            <Button href="/auth/login" width="full">
              Back to Login
            </Button>
          )}
        </div>
      </div>
      <div className="text-sm mt-6 text-center w-full text-text-03 mainUiBody mx-auto">
        <Link
          href="/auth/login"
          className="text-text-05 mainUiAction underline transition-colors duration-200"
        >
          Sign in another way
        </Link>
      </div>
    </div>
  );
}
