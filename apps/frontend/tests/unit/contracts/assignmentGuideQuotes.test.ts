/**
 * The user guide quotes button and state labels verbatim, in both languages.
 * A guide that names a control the screen does not have is worse than one that
 * stays vague: the reader hunts for it and concludes the feature is missing.
 *
 * Written after the ER28b guide draft quoted "Take" and "cannot act" while the
 * screen said "Assign to me" and "Owner cannot act". Nothing failed; the drift
 * was found by reading the locale file by hand.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import enProjectDetail from "@/locales/en/project_detail.json";
import koProjectDetail from "@/locales/ko/project_detail.json";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "../../../../..");
const GUIDE_EN = resolve(REPO, "docs-site/docs/user-guide/vulnerabilities.md");
const GUIDE_KO = resolve(
  REPO,
  "docs-site/i18n/ko/docusaurus-plugin-content-docs/current/user-guide/vulnerabilities.md",
);

/** Locale keys the assignment section of the guide quotes word for word. */
const QUOTED_KEYS = [
  "vulnerabilities.assignee.unassigned",
  "vulnerabilities.assignee.inactive",
  "vulnerabilities.assignee.mine",
  "vulnerabilities.assignee.assign_to_me",
  "vulnerabilities.assignee.take_over",
  "vulnerabilities.filter.assignee_all",
  "vulnerabilities.filter.assignee_me",
  "vulnerabilities.filter.assignee_unassigned",
] as const;

function lookup(bundle: unknown, dotted: string): string {
  const value = dotted
    .split(".")
    .reduce<unknown>(
      (node, part) => (node as Record<string, unknown>)?.[part],
      bundle,
    );
  if (typeof value !== "string") {
    throw new Error(`${dotted} is missing from the locale bundle`);
  }
  return value;
}

/**
 * Guides are hard-wrapped, so a label can straddle a line break. Collapsing
 * runs of whitespace is what lets the check read the sentence rather than the
 * layout; without it the contract would fail on rewrapping alone and would
 * teach people to delete it.
 */
function flow(markdown: string): string {
  return markdown.replace(/\s+/g, " ");
}

const en = flow(readFileSync(GUIDE_EN, "utf8"));
const ko = flow(readFileSync(GUIDE_KO, "utf8"));

describe("the assignment guide quotes labels that exist", () => {
  it.each(QUOTED_KEYS)("EN guide quotes %s", (key) => {
    expect(en).toContain(lookup(enProjectDetail, key));
  });

  it.each(QUOTED_KEYS)("KO guide quotes %s", (key) => {
    expect(ko).toContain(lookup(koProjectDetail, key));
  });

  it("is reading guides that still carry the section", () => {
    // Deleting the section would otherwise leave every case above passing on
    // whatever coincidental substrings remained elsewhere in the page.
    for (const guide of [en, ko]) {
      expect(guide).toContain("{#assignment-on-screen}");
      expect(guide).toContain("{#assignment-filter}");
    }
  });
});
