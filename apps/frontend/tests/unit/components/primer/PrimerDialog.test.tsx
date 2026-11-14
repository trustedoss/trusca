/**
 * PrimerDialog - the one modal wrapper.
 *
 * The reason it exists is the close button: Primer's own is labelled "Close"
 * in English whatever the UI language, so the wrapper supplies a translated
 * one. These tests pin that, and the open/close contract the callers rely on.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PrimerDialog } from "@/components/primer/PrimerDialog";
import i18n from "@/lib/i18n";

afterEach(async () => {
  await i18n.changeLanguage("en");
});

function renderDialog(onOpenChange = vi.fn(), open = true) {
  render(
    <PrimerDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Shortcuts"
      description="Keys that work everywhere"
      testId="the-dialog"
    >
      <p>body text</p>
    </PrimerDialog>,
  );
  return onOpenChange;
}

describe("PrimerDialog", () => {
  it("renders nothing while closed", () => {
    renderDialog(vi.fn(), false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("names the dialog by its title and carries the test id on the body", () => {
    renderDialog();
    expect(screen.getByRole("dialog", { name: "Shortcuts" })).toBeInTheDocument();
    expect(screen.getByTestId("the-dialog")).toHaveTextContent("body text");
  });

  it("closes from the close button", async () => {
    const onOpenChange = renderDialog();
    await userEvent.setup().click(screen.getByTestId("dialog-close"));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("closes on Escape", async () => {
    const onOpenChange = renderDialog();
    await userEvent.setup().keyboard("{Escape}");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("labels the close button in the UI language, not in Primer's English", async () => {
    await i18n.changeLanguage("ko");
    renderDialog();
    const close = screen.getByTestId("dialog-close");
    expect(close).toHaveAccessibleName("닫기");
    expect(screen.queryByRole("button", { name: "Close" })).not.toBeInTheDocument();
  });
});
