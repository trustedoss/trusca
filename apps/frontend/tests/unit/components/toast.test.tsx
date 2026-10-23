/**
 * ToastProvider / useToast — unit tests for W12-B.
 *
 * Coverage:
 *   - toast() renders the test-id contract the e2e harnesses depend on:
 *     `data-testid` (default "admin-toast"), `data-tone`, `data-toast-key`.
 *   - testId override (ScanCancelButton uses "scan-cancel-toast").
 *   - error tone maps to the destructive Alert.
 *   - multiple toasts queue and stack.
 *   - auto-dismiss removes a success toast after its ttl.
 *   - error toasts persist until closed, use role="alert", and a repeat
 *     under the same key replaces the earlier one.
 *   - every toast has a close button.
 *   - useToast outside a provider is a safe no-op (does not throw).
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider, useToast } from "@/components/ui/toast";

function Trigger({
  text,
  tone,
  toastKey,
  testId,
  ttlMs,
}: {
  text: string;
  tone?: "success" | "error";
  toastKey?: string;
  testId?: string;
  ttlMs?: number;
}) {
  const { toast } = useToast();
  return (
    <button
      onClick={() => toast(text, { tone, key: toastKey, testId, ttlMs })}
      data-testid={`fire-${text}`}
    >
      fire
    </button>
  );
}

describe("ToastProvider / useToast", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it("renders the admin-toast test-id contract by default", () => {
    render(
      <ToastProvider>
        <Trigger text="saved" toastKey="prefs_saved" />
      </ToastProvider>,
    );
    act(() => {
      fireEvent.click(screen.getByTestId("fire-saved"));
    });
    const el = screen.getByTestId("admin-toast");
    expect(el).toHaveAttribute("data-tone", "success");
    expect(el).toHaveAttribute("data-toast-key", "prefs_saved");
    expect(el).toHaveTextContent("saved");
  });

  it("honours a testId override (scan-cancel-toast)", () => {
    render(
      <ToastProvider>
        <Trigger text="cancelling" tone="error" testId="scan-cancel-toast" />
      </ToastProvider>,
    );
    act(() => {
      fireEvent.click(screen.getByTestId("fire-cancelling"));
    });
    expect(screen.getByTestId("scan-cancel-toast")).toHaveAttribute(
      "data-tone",
      "error",
    );
  });

  it("queues multiple toasts", () => {
    render(
      <ToastProvider>
        <Trigger text="one" toastKey="a" />
        <Trigger text="two" toastKey="b" />
      </ToastProvider>,
    );
    act(() => {
      fireEvent.click(screen.getByTestId("fire-one"));
      fireEvent.click(screen.getByTestId("fire-two"));
    });
    expect(screen.getAllByTestId("admin-toast")).toHaveLength(2);
  });

  it("auto-dismisses after the ttl", () => {
    render(
      <ToastProvider>
        <Trigger text="bye" toastKey="x" />
      </ToastProvider>,
    );
    act(() => {
      fireEvent.click(screen.getByTestId("fire-bye"));
    });
    expect(screen.getByTestId("admin-toast")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(screen.queryByTestId("admin-toast")).not.toBeInTheDocument();
  });

  it("is a safe no-op outside a provider", () => {
    // Rendering Trigger without ToastProvider must not throw on click.
    render(<Trigger text="orphan" />);
    expect(() => {
      act(() => {
        fireEvent.click(screen.getByTestId("fire-orphan"));
      });
    }).not.toThrow();
    expect(screen.queryByTestId("admin-toast")).not.toBeInTheDocument();
  });

  it("keeps an error toast until it is closed", () => {
    render(
      <ToastProvider>
        <Trigger text="failed" tone="error" toastKey="save_failed" />
      </ToastProvider>,
    );
    act(() => {
      fireEvent.click(screen.getByTestId("fire-failed"));
    });
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    const el = screen.getByTestId("admin-toast");
    expect(el).toHaveTextContent("failed");

    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
    });
    expect(screen.queryByTestId("admin-toast")).not.toBeInTheDocument();
  });

  it("still auto-dismisses an error toast given an explicit ttl", () => {
    render(
      <ToastProvider>
        <Trigger text="brief" tone="error" ttlMs={1000} />
      </ToastProvider>,
    );
    act(() => {
      fireEvent.click(screen.getByTestId("fire-brief"));
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.queryByTestId("admin-toast")).not.toBeInTheDocument();
  });

  it("announces errors as alerts and success as status", () => {
    render(
      <ToastProvider>
        <Trigger text="ok" toastKey="s" />
        <Trigger text="bad" tone="error" toastKey="e" />
      </ToastProvider>,
    );
    act(() => {
      fireEvent.click(screen.getByTestId("fire-ok"));
      fireEvent.click(screen.getByTestId("fire-bad"));
    });
    const [success, error] = screen.getAllByTestId("admin-toast");
    expect(success).toHaveAttribute("data-tone", "success");
    expect(success.querySelector('[role="status"]')).not.toBeNull();
    expect(success.querySelector('[role="alert"]')).toBeNull();
    expect(error).toHaveAttribute("data-tone", "error");
    expect(error.querySelector('[role="alert"]')).not.toBeNull();
  });

  it("lets a success toast be closed before it times out", () => {
    render(
      <ToastProvider>
        <Trigger text="saved" toastKey="s" />
      </ToastProvider>,
    );
    act(() => {
      fireEvent.click(screen.getByTestId("fire-saved"));
    });
    act(() => {
      fireEvent.click(screen.getByTestId("toast-dismiss"));
    });
    expect(screen.queryByTestId("admin-toast")).not.toBeInTheDocument();
  });

  it("replaces a repeated error with the same key instead of stacking it", () => {
    render(
      <ToastProvider>
        <Trigger text="again" tone="error" toastKey="sync_failed" />
        <Trigger text="other" tone="error" toastKey="other_failed" />
      </ToastProvider>,
    );
    act(() => {
      fireEvent.click(screen.getByTestId("fire-again"));
      fireEvent.click(screen.getByTestId("fire-other"));
      fireEvent.click(screen.getByTestId("fire-again"));
    });
    const keys = screen
      .getAllByTestId("admin-toast")
      .map((el) => el.getAttribute("data-toast-key"));
    expect(keys).toEqual(["other_failed", "sync_failed"]);
  });
});
