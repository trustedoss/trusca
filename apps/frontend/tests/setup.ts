import "@testing-library/jest-dom/vitest";

import { afterEach, beforeAll } from "vitest";
import { cleanup } from "@testing-library/react";

import i18n from "@/lib/i18n";

beforeAll(() => {
  // Radix popper-based primitives (DropdownMenu — backs the new MultiSelect,
  // ReleaseSwitcher, VEX menus) call a few DOM APIs that jsdom does not
  // implement. Polyfill them once globally so any test that opens a Radix
  // dropdown works without repeating the stubs per file.
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false;
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {};
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {};
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
  }
});

afterEach(() => {
  cleanup();
  // Reset to default language so a test that flips i18n state can't leak.
  void i18n.changeLanguage("en");
});
