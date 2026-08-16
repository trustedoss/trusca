/**
 * The `?` shortcut and the sheet it opens (C1).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  ShortcutHelpDialog,
  useShortcutHelpShortcut,
} from "@/components/ShortcutHelpDialog";

/** Mounts the hook and the sheet the way AppShell does, plus a text field. */
function Harness() {
  const { open, setOpen } = useShortcutHelpShortcut();
  return (
    <>
      <input data-testid="a-text-field" />
      <div data-testid="editable" contentEditable suppressContentEditableWarning />
      <span data-testid="open-state">{open ? "open" : "closed"}</span>
      <ShortcutHelpDialog open={open} onOpenChange={setOpen} />
    </>
  );
}

describe("useShortcutHelpShortcut", () => {
  it("opens on ?", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.keyboard("?");

    await waitFor(() => {
      expect(screen.getByTestId("shortcut-help-dialog")).toBeInTheDocument();
    });
  });

  it("stays shut while the reader is typing into a field", async () => {
    // `?` is a character, not a chord: unlike Cmd+K it lands inside every
    // search box and justification field in the product. Opening a help
    // sheet mid-sentence would be the shortcut fighting the user.
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByTestId("a-text-field"));
    await user.keyboard("why?");

    expect(screen.getByTestId("open-state").textContent).toBe("closed");
    expect(screen.getByTestId("a-text-field")).toHaveValue("why?");
  });

  it("stays shut inside a contenteditable region", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByTestId("editable"));
    await user.keyboard("?");

    expect(screen.getByTestId("open-state").textContent).toBe("closed");
  });

  it("ignores a modified ?", async () => {
    // On several layouts `?` is already a chord; a reader pressing one of
    // those means the character, or means something else entirely.
    const user = userEvent.setup();
    render(<Harness />);

    await user.keyboard("{Meta>}?{/Meta}");

    expect(screen.getByTestId("open-state").textContent).toBe("closed");
  });
});

describe("ShortcutHelpDialog", () => {
  it("lists every global shortcut the app has", async () => {
    render(<ShortcutHelpDialog open onOpenChange={() => {}} />);

    const rows = await screen.findAllByTestId("shortcut-help-row");
    // Cmd+K, ?, Esc. The list is short on purpose: a per-row Enter on a
    // focusable table row is the browser doing its job, not a binding, and
    // listing it would advertise something that does not exist.
    expect(rows).toHaveLength(3);
    expect(screen.getByText("?")).toBeInTheDocument();
    expect(screen.getByText("K")).toBeInTheDocument();
    expect(screen.getByText("Esc")).toBeInTheDocument();
  });
});
