/**
 * Read-only-demo write toast — v2.1 Track B (B5).
 *
 * Admin write mutations surface failures via the W12 global toast using the
 * shared pattern:
 *
 *   toast(t(adminErrorMessageKey(err)), {
 *     tone: "error",
 *     key: adminErrorExtension(err),
 *   })
 *
 * This test drives that exact path through a REAL `ToastProvider` (toasts are a
 * no-op outside a provider, so the assertion must wrap one) and confirms that a
 * write blocked by the read-only-demo guard produces a toast that is
 * DISTINGUISHABLE — by its `data-toast-key` and its copy — from a plain
 * permission-denied 403.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider, useTranslation } from "react-i18next";
import { describe, expect, it } from "vitest";

import {
  adminErrorExtension,
  adminErrorMessageKey,
} from "@/features/admin/lib/adminErrorMessage";
import { DEMO_READ_ONLY_PROBLEM_TYPE } from "@/lib/demoReadOnly";
import i18n from "@/lib/i18n";
import { ProblemError, type ProblemDetails } from "@/lib/problem";
import { ToastProvider, useToast } from "@/components/ui/toast";

function demoError(): ProblemError {
  const problem: ProblemDetails = {
    type: DEMO_READ_ONLY_PROBLEM_TYPE,
    title: "Read-only demo",
    status: 403,
    detail: "writes disabled",
    demo_read_only: true,
  };
  return new ProblemError("Read-only demo", {
    status: 403,
    title: "Read-only demo",
    detail: problem.detail,
    problem,
  });
}

function forbiddenError(): ProblemError {
  const problem: ProblemDetails = {
    type: "about:blank",
    title: "Forbidden",
    status: 403,
    detail: "no access",
  };
  return new ProblemError("Forbidden", {
    status: 403,
    title: "Forbidden",
    detail: problem.detail,
    problem,
  });
}

/** Mirrors the admin mutation onError handler exactly. */
function Harness({ err }: { err: unknown }) {
  const { t } = useTranslation("admin");
  const { toast } = useToast();
  return (
    <button
      type="button"
      data-testid="fire"
      onClick={() =>
        toast(t(adminErrorMessageKey(err)), {
          tone: "error",
          key: adminErrorExtension(err),
        })
      }
    >
      fire
    </button>
  );
}

function renderWith(err: unknown) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ToastProvider>
        <Harness err={err} />
      </ToastProvider>
    </I18nextProvider>,
  );
}

describe("read-only-demo write toast", () => {
  it("tags the demo 403 toast with data-toast-key=demo_read_only", async () => {
    const user = userEvent.setup();
    renderWith(demoError());
    await user.click(screen.getByTestId("fire"));

    const toast = await screen.findByTestId("admin-toast");
    expect(toast).toHaveAttribute("data-toast-key", "demo_read_only");
    expect(toast).toHaveAttribute("data-tone", "error");
    // Friendly demo copy, not a permission-denied message.
    expect(toast).toHaveTextContent(/read-only/i);
  });

  it("is distinguishable from an ordinary permission-denied 403", async () => {
    const user = userEvent.setup();
    renderWith(forbiddenError());
    await user.click(screen.getByTestId("fire"));

    const toast = await screen.findByTestId("admin-toast");
    // A plain 403 is NOT the demo key.
    expect(toast).toHaveAttribute("data-toast-key", "unknown");
    expect(toast).not.toHaveTextContent(/read-only live demo/i);
  });
});
