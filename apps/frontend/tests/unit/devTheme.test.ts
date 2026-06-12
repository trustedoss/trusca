/**
 * Dev-only AIS theme prototype switch (src/lib/devTheme.ts).
 *
 * The contract under test: the <html> class and the localStorage flag move
 * together, and initAisTheme() restores the persisted flag on boot — that is
 * what keeps the theme stable across reloads on any route, login included.
 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  AIS_STORAGE_KEY,
  AIS_THEME_CLASS,
  initAisTheme,
  isAisThemeEnabled,
  setAisTheme,
} from "@/lib/devTheme";

describe("devTheme", () => {
  beforeEach(() => {
    window.localStorage.removeItem(AIS_STORAGE_KEY);
    document.documentElement.classList.remove(AIS_THEME_CLASS);
  });

  it("setAisTheme(true) applies the html class and persists the flag", () => {
    setAisTheme(true);

    expect(document.documentElement.classList.contains(AIS_THEME_CLASS)).toBe(
      true,
    );
    expect(window.localStorage.getItem(AIS_STORAGE_KEY)).toBe("1");
    expect(isAisThemeEnabled()).toBe(true);
  });

  it("setAisTheme(false) removes the class and clears the flag", () => {
    setAisTheme(true);
    setAisTheme(false);

    expect(document.documentElement.classList.contains(AIS_THEME_CLASS)).toBe(
      false,
    );
    expect(window.localStorage.getItem(AIS_STORAGE_KEY)).toBeNull();
    expect(isAisThemeEnabled()).toBe(false);
  });

  it("initAisTheme restores a persisted flag on boot", () => {
    window.localStorage.setItem(AIS_STORAGE_KEY, "1");

    initAisTheme();

    expect(document.documentElement.classList.contains(AIS_THEME_CLASS)).toBe(
      true,
    );
  });

  it("initAisTheme clears a stale class when no flag is persisted", () => {
    document.documentElement.classList.add(AIS_THEME_CLASS);

    initAisTheme();

    expect(document.documentElement.classList.contains(AIS_THEME_CLASS)).toBe(
      false,
    );
  });
});
