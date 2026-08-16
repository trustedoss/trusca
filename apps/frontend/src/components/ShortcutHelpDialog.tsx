// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * The keyboard shortcuts, listed where someone can find them (C1).
 *
 * The app has had exactly one global shortcut and no way to learn it. The
 * only hint was a small kbd glyph inside the search button, and that button
 * is hidden on a phone, so the one shortcut the product had was also the
 * one thing it never told you about.
 *
 * `?` is the convention for this, and it costs nothing to hold open: the
 * sheet is a plain list, not a surface anyone has to maintain per screen.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * True when a keystroke belongs to whatever the user is typing into.
 *
 * `?` is a character, not a chord, so unlike Cmd+K it lands inside every
 * search box and text area in the product. Without this the help sheet
 * opens while someone types a question into a justification field.
 */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  // Walk to the nearest contenteditable ancestor rather than reading
  // `isContentEditable`: the property is inherited but unimplemented in
  // jsdom, so relying on it would leave this branch untestable.
  const editable = target.closest<HTMLElement>("[contenteditable]");
  if (editable && editable.getAttribute("contenteditable") !== "false") {
    return true;
  }
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/** Opens the sheet on `?`, the way `useCommandMenuShortcut` opens on Cmd+K. */
export function useShortcutHelpShortcut(): {
  open: boolean;
  setOpen: (open: boolean) => void;
} {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key !== "?") return;
      // A modifier means the reader meant something else; on several layouts
      // `?` is already a chord, so only the bare character counts.
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTypingTarget(event.target)) return;
      event.preventDefault();
      setOpen((prev) => !prev);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return { open, setOpen };
}

interface Shortcut {
  /** Rendered inside a <kbd>. */
  keys: string[];
  labelKey: string;
}

// Every global shortcut the app has. A local Enter-to-open-a-row is not one
// of these: it is the browser doing what it does with a focusable element,
// and listing it would suggest a binding that does not exist.
const SHORTCUTS: Shortcut[] = [
  { keys: ["⌘", "K"], labelKey: "shortcuts.command_menu" },
  { keys: ["?"], labelKey: "shortcuts.help" },
  { keys: ["Esc"], labelKey: "shortcuts.dismiss" },
];

export function ShortcutHelpDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" data-testid="shortcut-help-dialog">
        <DialogHeader>
          <DialogTitle>{t("shortcuts.title")}</DialogTitle>
          <DialogDescription>{t("shortcuts.description")}</DialogDescription>
        </DialogHeader>
        <ul className="divide-y" data-testid="shortcut-help-list">
          {SHORTCUTS.map((shortcut) => (
            <li
              key={shortcut.labelKey}
              className="flex items-center justify-between gap-4 py-2.5 text-sm"
              data-testid="shortcut-help-row"
            >
              <span>{t(shortcut.labelKey)}</span>
              <span className="flex shrink-0 items-center gap-1">
                {shortcut.keys.map((key) => (
                  <kbd
                    key={key}
                    className="rounded border bg-muted px-1.5 py-0.5 font-mono text-xs"
                  >
                    {key}
                  </kbd>
                ))}
              </span>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
