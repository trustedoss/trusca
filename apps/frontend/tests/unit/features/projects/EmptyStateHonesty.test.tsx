/**
 * What the first-scan path's empty states claim (C3).
 *
 * The unit's criterion is not that these have buttons but that none of them
 * asserts something it cannot know. Three said things that were false on a
 * project nobody had scanned: a components table blamed filters nobody had
 * set, a source viewer described a decision made by a scan that had never
 * run, and a policy list told a reader to write something they may not be
 * allowed to write.
 */
import { describe, expect, it } from "vitest";

import en from "@/locales/en/policies.json";
import projectDetailEn from "@/locales/en/project_detail.json";
import projectDetailKo from "@/locales/ko/project_detail.json";

describe("first-scan empty-state copy", () => {
  it("does not blame filters for an empty components table", () => {
    // The unfiltered case is the one a new project lands in, and the reader
    // has touched no filter. Telling them to adjust one sends them hunting.
    const empty = projectDetailEn.components.empty;
    expect(empty.unfiltered_subtitle).not.toContain("filter");
    expect(empty.unfiltered_subtitle).not.toContain("Adjust");
    // The filtered case still may, because there the filter is the reason.
    expect(empty.subtitle).toContain("filters");
  });

  it("does not make an absent scan the subject of a sentence", () => {
    // The source tree 404s both when no scan has succeeded and when the one
    // that ran did not keep the files. The old copy picked the second and
    // told the reader to "re-scan" something they had never scanned.
    const source = projectDetailEn.source.empty;
    expect(source.title).not.toContain("this scan");
    // It names no causes at all. The service returns the same 404 whether
    // the scan preserved nothing, never ran, or had its tarball reclaimed by
    // the retention sweeper, and the third is the common one for anyone
    // reading a pinned older release.
    expect(source.description).not.toContain("Either");
    expect(source.description).not.toContain("Re-scan");
    expect(source.description).toContain("none is available");
  });

  it("says the same in Korean, no more and no less", () => {
    // Two units of this track shipped Korean that claimed more than the
    // English beside it.
    const ko = projectDetailKo.source.empty;
    expect(ko.description).toContain("쓸 수 있는 것이 없습니다");
    expect(ko.description).not.toContain("않았거나");
  });

  it("separates a missing policy from an instruction to write one", () => {
    // The old string was both at once, and its instruction was wrong for a
    // developer: they can open the drawer and find it read-only.
    expect(en.policies.empty).not.toContain(".");
    expect(en.policies.empty_hint_can_author).toContain("Pick a team");
    expect(en.policies.empty_hint_cannot_author).toContain(
      "team administrator",
    );
    // Both say what applies meanwhile, so an empty list does not read as
    // "nothing is checked".
    for (const hint of [
      en.policies.empty_hint_can_author,
      en.policies.empty_hint_cannot_author,
    ]) {
      expect(hint).toContain("built-in license categories");
    }
  });

  it("keeps the recent-scans hint to this table", () => {
    // The first attempt claimed the screen showed nothing until a scan ran,
    // which the info card and the gate card above it disprove on sight.
    const hint = projectDetailEn.overview.recent_scans.empty_hint;
    expect(hint).toContain("has not been scanned");
    expect(hint).not.toContain("shows nothing");
  });
});
