# ai-review - findings-driven AI triage, and a whole-diff reviewer for forks

Two related but separate tools. Neither gates anything.

- `review.py` - parses a semgrep SARIF report and a Trivy JSON report, pulls
  the surrounding source lines, sends a capped set to the Messages API, and
  renders the verdicts as a comment body. Reviews what a scanner already
  flagged.
- `external_review.py` - reads the whole diff of a pull request opened from a
  fork and asks the model for a code review, a security review, and one
  explicit recommendation on whether the pending CI workflow run looks safe
  to approve. Reviews everything, not just what a scanner flagged, because a
  fork PR has had no scanner run over it yet and no maintainer has read it.
- `selftest.py` / `external_review_selftest.py` - drive the two above
  offline, with a stub in place of the HTTP call. Both run in CI as part of
  `lint (backend)`.

Callers: `.github/workflows/ai-review.yml` (same-repo pull requests, semgrep
findings), `.github/workflows/sca-self.yml` (nightly, dependency findings),
and `.github/workflows/external-pr-review.yml` (fork pull requests, whole
diff). See that last file's header comment for why it uses
`pull_request_target` when `ai-review.yml`'s own header says not to - the
short version is that it never checks out a fork's code, only reads its diff
as text, and its only side effect is a comment.

## Run it

```bash
python tools/ai-review/selftest.py                     # offline checks, no key needed
python tools/ai-review/external_review_selftest.py      # same, for the fork reviewer

ANTHROPIC_API_KEY=... python tools/ai-review/review.py \
    --semgrep semgrep.sarif \
    --out review_result.md \
    --state review_state.txt

ANTHROPIC_API_KEY=... python tools/ai-review/external_review.py \
    --diff pr.diff \
    --meta pr_meta.json \
    --out review_result.md \
    --state review_state.txt
```

`review.py --state` receives one of three words, and the caller is expected to
branch on it:

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

`external_review.py --state` only has two words, because there is no "clean"
case for a whole diff - a non-empty diff always gets a review:

| state | meaning                          | what the workflow does           |
| ----- | -------------------------------- | --------------------------------- |
| `ok`  | a review was produced            | post a comment, or edit its own   |
| `error` | empty diff, call failed, or returned nothing | leave any existing comment alone |

## Decisions worth keeping

**It re-runs semgrep instead of reading `sast.yml`'s report.** That job runs
`--severity=ERROR --error`, so its SARIF holds ERROR findings only and the
build is already red whenever the file is non-empty. Nothing is left to
triage. Dropping both flags is what produces WARNING-level candidates.

**It calls REST, not the SDK.** Everything else this repository installs is
pinned to an exact version, and there is no SDK version we can pin today and
be sure still resolves on the day someone provisions the key - a wrong pin
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
looks complete is worse than no list. `external_review.py` caps the diff
itself at `MAX_DIFF_CHARS` for the same reason, and says so in the comment
when it truncates.

**`external_review.py` gets the stronger model.** `review.py` triages a
handful of lines a scanner already pointed at; `external_review.py` reads an
entire, unread, external diff and is the thing a maintainer's decision to
approve a workflow run actually rests on. The harder judgment gets the
stronger model.

**Why `external_review.py` is a second file rather than a mode of `review.py`.**
The input shape is different (a diff and a description, not scanner output),
the model is different, and the system prompt asks a different question:
"is this diff safe" rather than "is this finding real". Forcing both through
one script would mean branching on which kind of input arrived at nearly every
function; two files that each do one thing stayed easier to read and to test.

## Not verified yet

There is no `ANTHROPIC_API_KEY` in this repository, so the network hop has
never run for either script: whether the endpoint accepts the payload and
whether the model name resolves are open questions until someone provisions a
key. Everything on either side of that hop is covered by `selftest.py` and
`external_review_selftest.py`. Confirm the first live run of each posts a
comment and leaves the build green.
