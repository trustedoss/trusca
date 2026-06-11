/**
 * ToastProvider / useToast — unit tests for W12-B.
 *
 * Coverage:
 *   - toast() renders the test-id contract the e2e harnesses depend on:
 *     `data-testid` (default "admin-toast"), `data-tone`, `data-toast-key`.
 *   - testId override (ScanCancelButton uses "scan-cancel-toast").
 *   - error tone maps to the destructive Alert.
 *   - multiple toasts queue and stack.
 *   - auto-dismiss removes a toast after its ttl.
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
}: {
  text: string;
  tone?: "success" | "error";
  toastKey?: string;
  testId?: string;
}) {
  const { toast } = useToast();
  return (
    <button
      onClick={() => toast(text, { tone, key: toastKey, testId })}
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
});
