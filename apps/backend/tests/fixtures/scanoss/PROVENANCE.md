# SCANOSS fixture provenance

`vendored-tree-osskb.json` is a **recorded response**, not a hand-written one.
It exists because the coverage filter it tests is a precision/recall trade-off,
and the only honest way to judge one is against what the service actually
returns. A minimal JSON written to match the code's expectations would agree
with the code by construction.

## How it was recorded

`POST https://api.osskb.org/scan/direct` — the free, unauthenticated Open Source
Knowledge Base endpoint that `SCANOSS_API_URL` defaults to — with a WFP
fingerprint file listing seven files by MD5 and size. Recorded 2026-08-12
against server 5.4.25, KB `26.07` monthly / `26.08.12` daily.

The seven files are upstream releases fetched from their canonical repositories
and laid out under `third_party/` as a project vendoring them would:

| Path in the fingerprint | Upstream |
|---|---|
| `third_party/cjson/cJSON.c`, `cJSON.h`, `cJSON_Utils.c` | DaveGamble/cJSON v1.7.18 |
| `third_party/inih/ini.c`, `ini.h` | benhoyt/inih r58 |
| `third_party/stb/stb_image.h` | nothings/stb (master) |
| `third_party/linenoise/linenoise.c` | antirez/linenoise (master) |

No source code is stored here — a WFP carries hashes, and this file holds only
the service's answer.

## What it captures that a synthetic fixture would not

- **One library answered under two identities.** `cJSON_Utils.c` comes back as
  component `github.com/DaveGamble/cJSON` with a Go pseudo-version, while its
  two siblings come back as `cJSON` / `v1.7.18`. Grouping by component *name*
  splits them; grouping by purl keeps the siblings together and leaves the Go
  entry as its own single-file component, which is what the service said.
- **Version disagreement inside one library.** `ini.c` answers `r58` and
  `ini.h` answers `r54`, from the same purl. Any consensus rule has to decide
  something here, and a fixture where every file agrees never exercises it.
- **Legitimate single-file components.** `stb_image.h` and `linenoise.c` are
  single-file libraries — the most ordinary shape vendored code takes. A
  minimum-file-count filter drops them, which is the measurement that decided
  this repository against adopting one.

## Re-recording

The KB moves, so a re-record will differ. That is fine; what must not change
silently is a verdict. Re-record deliberately, diff the JSON, and update the
assertions with an argument for each change rather than refreshing until green.
