/**
 * CopyButton: unit tests (A6).
 *
 * The two things worth holding down are the ones that are invisible when
 * they break: that the value reaching the clipboard is the whole string, and
 * that a browser without the async clipboard API still copies rather than
 * reporting a failure the user cannot act on.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CopyButton } from "@/components/ui/copy-button";
import { writeToClipboard } from "@/lib/clipboard";
import { ToastProvider } from "@/components/ui/toast";

function renderButton(value: string, label = "CVE id") {
  return render(
    <ToastProvider>
      <CopyButton value={value} label={label} data-testid="copy" />
    </ToastProvider>,
  );
}

describe("CopyButton", () => {
  let originalClipboard: typeof navigator.clipboard | undefined;
  let originalExecCommand: typeof document.execCommand | undefined;

  beforeEach(() => {
    originalClipboard = navigator.clipboard;
    originalExecCommand = document.execCommand;
  });

  afterEach(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: originalClipboard,
    });
    // Restored too: a stub left behind here reaches the next test in the
    // file, and one of them depends on the copy failing.
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: originalExecCommand,
    });
    vi.restoreAllMocks();
  });

  function stubClipboard(writeText: (v: string) => Promise<void>) {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
  }

  it("copies the whole value and says so", async () => {
    const user = userEvent.setup();
    const written: string[] = [];
    stubClipboard(async (v) => {
      written.push(v);
    });

    // A long purl: the kind of string that is truncated on screen, which is
    // why copying half of it by dragging was the problem.
    const purl = "pkg:maven/org.example/some-artifact@1.2.3?type=jar";
    renderButton(purl, "package URL");
    await user.click(screen.getByTestId("copy"));

    await waitFor(() => expect(written).toEqual([purl]));
    expect(screen.getByTestId("admin-toast").textContent).toContain(
      "package URL copied",
    );
  });

  it("names what it copies, so four buttons do not say the same thing", async () => {
    stubClipboard(async () => {});
    renderButton("CVE-2026-0001", "CVE id");

    expect(screen.getByTestId("copy").getAttribute("aria-label")).toBe(
      "Copy CVE id",
    );
  });

  it("falls back when the browser exposes no clipboard API", async () => {
    // setup() installs a clipboard stub of its own, so it runs first and
    // the absence we are testing is established after it.
    const user = userEvent.setup();
    // The real case, not a hypothetical: `navigator.clipboard` is absent on
    // any origin the browser considers insecure, and this product is
    // installed on internal networks that serve plain http.
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    const exec = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: exec,
    });

    renderButton("CVE-2026-0002");
    await user.click(screen.getByTestId("copy"));

    await waitFor(() => expect(exec).toHaveBeenCalledWith("copy"));
    expect(screen.getByTestId("admin-toast").getAttribute("data-tone")).toBe(
      "success",
    );
  });

  it("falls back when the API is present but refuses", async () => {
    const user = userEvent.setup();
    stubClipboard(async () => {
      throw new Error("NotAllowedError");
    });
    const exec = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: exec,
    });

    renderButton("CVE-2026-0003");
    await user.click(screen.getByTestId("copy"));

    await waitFor(() => expect(exec).toHaveBeenCalledWith("copy"));
  });

  it("tells the user to copy by hand when nothing worked", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn().mockReturnValue(false),
    });

    renderButton("CVE-2026-0004", "CVE id");
    await user.click(screen.getByTestId("copy"));

    const toast = await screen.findByTestId("admin-toast");
    expect(toast.getAttribute("data-tone")).toBe("error");
    // A failure the user can act on: the value is on screen, so selecting it
    // is a real instruction rather than an apology.
    expect(toast.textContent).toContain("copy manually");
  });

  it("leaves nothing behind when the copy command throws", async () => {
    // The path that actually leaked. `document.execCommand` is deprecated,
    // so a browser that drops it makes this call throw, and the removal used
    // to sit on the line after it: every attempt left another focusable
    // textarea in the document.
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn(() => {
        throw new TypeError("execCommand is not a function");
      }),
    });

    expect(await writeToClipboard("x")).toBe(false);
    expect(document.querySelectorAll("textarea")).toHaveLength(0);
  });

  it("keeps the scratch element out of the tab order while it exists", async () => {
    // Off-screen is not out of reach: without these it sat in the tab order
    // and in the accessibility tree for as long as it was mounted.
    let seen: { tabIndex: string | null; ariaHidden: string | null } | null =
      null;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn(() => {
        const el = document.querySelector("textarea");
        seen = {
          tabIndex: el?.getAttribute("tabindex") ?? null,
          ariaHidden: el?.getAttribute("aria-hidden") ?? null,
        };
        return true;
      }),
    });

    await writeToClipboard("x");

    expect(seen).toEqual({ tabIndex: "-1", ariaHidden: "true" });
  });

  it("leaves nothing behind in the document", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn().mockReturnValue(true),
    });

    await writeToClipboard("x");

    expect(document.querySelectorAll("textarea")).toHaveLength(0);
  });
});
