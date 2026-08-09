# ai-review — findings-driven AI triage

Turns what the level 3 scanners flagged into a ranked reading order. It gates
nothing.

- `review.py` — parses a semgrep SARIF report and a Trivy JSON report, pulls
  the surrounding source lines, sends a capped set to the Messages API, and
  renders the verdicts as a comment body.
- `selftest.py` — drives all of that offline, with a stub in place of the
  HTTP call. Runs in CI as part of `lint (backend)`.

Callers: `.github/workflows/ai-review.yml` (pull requests, semgrep findings)
and `.github/workflows/sca-self.yml` (nightly, dependency findings).

## Run it

```bash
python tools/ai-review/selftest.py          # offline checks, no key needed

ANTHROPIC_API_KEY=... python tools/ai-review/review.py \
    --semgrep semgrep.sarif \
    --out review_result.md \
    --state review_state.txt
```

`--state` receives one of three words, and the caller is expected to branch on
it:

| state      | meaning                          | what the workflow does           |
| ---------- | -------------------------------- | -------------------------------- |
| `findings` | verdicts were produced           | post a comment, or edit its own  |
| `clean`    | the scanners flagged nothing     | edit an existing comment only    |
| `error`    | the call failed or returned none | leave any existing comment alone |

`clean` never opens a comment: one on every healthy pull request is noise.
`error` touches nothing, because a failed call is not evidence that the
earlier findings went away.

`--bare` drops the marker and the framing paragraph so the output can be
folded into a body that carries its own, which is what the nightly scan does.

## Decisions worth keeping

**It re-runs semgrep instead of reading `sast.yml`'s report.** That job runs
`--severity=ERROR --error`, so its SARIF holds ERROR findings only and the
build is already red whenever the file is non-empty. Nothing is left to
triage. Dropping both flags is what produces WARNING-level candidates.

**It calls REST, not the SDK.** Everything else this repository installs is
pinned to an exact version, and there is no SDK version we can pin today and
be sure still resolves on the day someone provisions the key — a wrong pin
fails the install and leaves the feature quietly dead. The `anthropic-version`
header pins the wire format instead, and the standard library covers the rest.

**The model name is pinned** (`MODEL` in `review.py`). An alias would change
how findings are judged between one run and the next.

**Findings are fenced as untrusted data.** The prompt wraps them in explicit
markers and the system prompt says instructions inside them are to be reported
rather than followed. On a fork pull request, that text is written by whoever
opened it. Mentions in the model's output are also defused before the comment
is posted, so a crafted finding cannot make the bot ping people.

**Caps are disclosed.** At most 8 semgrep and 5 dependency findings go out;
when anything is dropped the comment says how many. A truncated list that
looks complete is worse than no list.

## Not verified yet

There is no `ANTHROPIC_API_KEY` in this repository, so the network hop has
never run: whether the endpoint accepts this payload and whether the model
name resolves are open questions until someone provisions a key. Everything on
either side of that hop is covered by `selftest.py`. Confirm the first live
run posts a comment and leaves the build green.
