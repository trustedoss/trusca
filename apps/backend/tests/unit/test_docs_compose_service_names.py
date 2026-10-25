"""Every compose service a documented command names exists in that compose file.

The production stack split the single ``worker`` service into ``worker-scan``,
``worker-default`` and ``worker-engine``, and about sixty commands in the
guides (EN and KO) kept naming ``worker`` (#582). ``docker-compose exec
worker ...`` then fails with ``No such service: worker``, which is the first
thing an operator sees in the middle of an incident.

The check reads every ``docker-compose`` command in the guides' code (fenced
blocks and inline code spans), in the README, in ``.env.example`` and in
``scripts/``. It works out which compose file a command targets from its
``-f`` options (``docker-compose.yml`` when there is none) and requires each
service the command names to be a service of that file: the positional
arguments of a service-taking subcommand, and the services in ``--scale
SERVICE=N`` and ``--exit-code-from SERVICE``. A ``-f`` file that is not in the
repository fails the check rather than being read as ``docker-compose.yml``.
Prose such as "restart the worker" is not a command and is not checked.

Out of scope: ``~~~`` fences (the guides use backtick fences only), and
commands whose ``-f`` value is a shell variable such as ``$COMPOSE_FILE``
(the scripts pick the file at run time, so there is no file to check against).
"""

from __future__ import annotations

import pathlib
import re
import shlex
from dataclasses import dataclass

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
COMPOSE_PROD = REPO_ROOT / "docker-compose.yml"
DOCS_ROOTS = (
    REPO_ROOT / "docs-site" / "docs",
    REPO_ROOT / "docs-site" / "i18n" / "ko" / "docusaurus-plugin-content-docs" / "current",
)
MARKDOWN_PAGES = (REPO_ROOT / "README.md",)
#: Plain-text sources: every line is read as a possible command.
TEXT_SOURCES = (REPO_ROOT / ".env.example",)
SCRIPTS_ROOT = REPO_ROOT / "scripts"

#: An operator-written file the guides tell readers to create. It overrides
#: services of the file it is layered on and adds none of its own.
OVERRIDE_FILE = "docker-compose.override.yml"

#: docker-compose options before the subcommand that take a value.
_GLOBAL_VALUE_OPTIONS = frozenset(
    {
        "-f",
        "--file",
        "-p",
        "--project-name",
        "--profile",
        "--env-file",
        "--project-directory",
        "--ansi",
        "-H",
        "--host",
        "--log-level",
    }
)

#: Subcommand -> its options that take a value (docker-compose 1.29). Every
#: other option is a flag; ``-t`` is a value for ``up``/``stop``/``restart``
#: (timeout) but a flag for ``logs`` (timestamps), and ``-s`` a value for
#: ``kill`` (signal) but a flag for ``rm`` (stop first).
_VALUE_OPTIONS: dict[str, frozenset[str]] = {
    "logs": frozenset({"--tail", "--since", "--until"}),
    "up": frozenset({"-t", "--timeout", "--scale", "--exit-code-from"}),
    "restart": frozenset({"-t", "--timeout"}),
    "stop": frozenset({"-t", "--timeout"}),
    "start": frozenset(),
    "ps": frozenset({"--filter"}),
    "kill": frozenset({"-s", "--signal"}),
    "rm": frozenset(),
    "pull": frozenset(),
    "top": frozenset(),
    "pause": frozenset(),
    "unpause": frozenset(),
    "create": frozenset(),
    "build": frozenset({"--build-arg", "-m", "--memory", "--progress"}),
    "events": frozenset(),
    "exec": frozenset({"-u", "--user", "-w", "--workdir", "-e", "--env", "--index"}),
    "run": frozenset(
        {
            "-u",
            "--user",
            "-w",
            "--workdir",
            "-e",
            "--env",
            "--name",
            "--entrypoint",
            "-v",
            "--volume",
            "-p",
            "--publish",
            "-l",
            "--label",
        }
    ),
    "port": frozenset({"--protocol", "--index"}),
}
#: Subcommands whose first positional argument is the service and the rest a
#: command run inside it. For the others every positional is a service.
_SINGLE_SERVICE = frozenset({"exec", "run", "port"})
#: Options whose value names a service, alone or as ``SERVICE=N``.
_SERVICE_VALUE_OPTIONS = frozenset({"--scale", "--exit-code-from"})

#: Shell characters that end a command.
_OPERATOR_CHARS = "|;&<>()"
_FENCE = re.compile(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", re.M | re.S)
_INLINE = re.compile(r"(`+)(.+?)\1", re.S)


@dataclass(frozen=True)
class Command:
    page: pathlib.Path
    text: str
    compose_files: tuple[str, ...]
    unknown_files: tuple[str, ...]
    subcommand: str
    services: tuple[str, ...]

    @property
    def locale(self) -> str:
        return "ko" if "i18n" in self.page.parts else "en"


def _services_of(name: str) -> frozenset[str]:
    document = yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8")) or {}
    return frozenset(document.get("services") or {})


def _markdown_pages() -> list[pathlib.Path]:
    pages = [
        path for root in DOCS_ROOTS for path in sorted(root.rglob("*.md*")) if path.is_file()
    ]
    return pages + [path for path in MARKDOWN_PAGES if path.is_file()]


def _text_sources() -> list[pathlib.Path]:
    scripts = [
        path
        for path in sorted(SCRIPTS_ROOT.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    ]
    return [path for path in TEXT_SOURCES if path.is_file()] + scripts


def _code_fragments(text: str) -> list[str]:
    """Fenced block bodies, then the inline code spans of the remaining prose."""
    fragments = [body.replace("\\\n", " ") for body in _FENCE.findall(text)]
    prose = _FENCE.sub("", text)
    fragments += [span for _, span in _INLINE.findall(prose)]
    return fragments


def _is_placeholder(token: str) -> bool:
    return any(mark in token for mark in "<>${}*") or not token


def _tokens(rest: str) -> list[str]:
    """Shell words up to the first operator (pipe, ``&&``, redirect, ...)."""
    lexer = shlex.shlex(rest, posix=True, punctuation_chars=_OPERATOR_CHARS)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    words: list[str] = []
    try:
        for word in lexer:
            if word and set(word) <= set(_OPERATOR_CHARS):
                if word[0] in "<>" and words and words[-1].isdigit():
                    # ``2>/dev/null``: the digit is the redirect's descriptor.
                    words.pop()
                break
            words.append(word)
    except ValueError:
        # An unclosed quote: the line continues past this fragment (or is
        # inside an echo string). Keep the words read so far, they hold the
        # subcommand and the service.
        pass
    return words


def _parse(page: pathlib.Path, line: str) -> list[Command]:
    commands = []
    for start in (m.end() for m in re.finditer(r"\bdocker-compose\b", line)):
        tokens = _tokens(line[start:])
        files: list[str] = []
        variable_file = False
        index = 0
        while index < len(tokens) and tokens[index].startswith("-"):
            name, _, inline = tokens[index].partition("=")
            if name in _GLOBAL_VALUE_OPTIONS:
                value = inline or (tokens[index + 1] if index + 1 < len(tokens) else "")
                if name in {"-f", "--file"}:
                    if "$" in value:
                        variable_file = True
                    files.append(pathlib.PurePosixPath(value).name)
                index += 1 if inline else 2
            else:
                index += 1
        if variable_file or index >= len(tokens):
            continue
        subcommand = tokens[index]
        if subcommand not in _VALUE_OPTIONS:
            continue
        value_options = _VALUE_OPTIONS[subcommand]
        named: list[str] = []
        index += 1
        while index < len(tokens):
            token = tokens[index]
            if token.startswith("-"):
                name, _, inline = token.partition("=")
                takes_value = name in value_options
                value = inline or (
                    tokens[index + 1] if takes_value and index + 1 < len(tokens) else ""
                )
                if name in _SERVICE_VALUE_OPTIONS and value:
                    named.append(value.partition("=")[0])
                index += 2 if takes_value and not inline else 1
                continue
            named.append(token)
            if subcommand in _SINGLE_SERVICE:
                # The rest is the command run inside the service.
                break
            index += 1
        in_repo = tuple(f for f in files if (REPO_ROOT / f).is_file())
        unknown = tuple(f for f in files if f not in in_repo and f != OVERRIDE_FILE)
        commands.append(
            Command(
                page=page,
                text=" ".join(["docker-compose", *tokens]),
                compose_files=in_repo or (COMPOSE_PROD.name,),
                unknown_files=unknown,
                subcommand=subcommand,
                services=tuple(p for p in named if not _is_placeholder(p)),
            )
        )
    return commands


def _commands() -> list[Command]:
    found = []
    for page in _markdown_pages():
        for fragment in _code_fragments(page.read_text(encoding="utf-8")):
            for line in fragment.splitlines():
                found += _parse(page, line)
    for source in _text_sources():
        try:
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line in text.splitlines():
            # A comment or message that quotes a command in backticks: only
            # the quoted part is the command.
            spans = [span for _, span in _INLINE.findall(line)]
            for fragment in spans or [line]:
                found += _parse(source, fragment)
    return found


COMMANDS = _commands()


def test_the_scan_finds_the_commands_it_is_about() -> None:
    """Not vacuous: the guides name services in well over a hundred commands,
    in each locale, in the exec, logs and restart forms, and the plain-text
    sources are read too."""
    with_services = [c for c in COMMANDS if c.services]
    for locale in ("en", "ko"):
        in_locale = [
            c for c in with_services if c.locale == locale and c.page.suffix in {".md", ".mdx"}
        ]
        assert len(in_locale) > 80, f"only {len(in_locale)} {locale} commands found"
        assert {"exec", "logs", "restart", "up"} <= {c.subcommand for c in in_locale}, locale
        # The private-CA page's log command, which an earlier, narrower check
        # on that page alone used to cover.
        assert any(
            c.page.name == "private-ca.md" and c.subcommand == "logs" for c in in_locale
        ), f"the {locale} private-CA log command was not found"
    assert any(
        c.compose_files == ("docker-compose.dev.yml",) for c in with_services
    ), "no dev-stack command found, so the -f handling is not exercised"
    assert any(
        c.page.name == ".env.example" for c in with_services
    ), "no .env.example command found"
    assert any(
        SCRIPTS_ROOT in c.page.parents for c in with_services
    ), "no scripts/ command found"


def test_the_parser_reads_the_forms_the_guides_use() -> None:
    page = REPO_ROOT / "README.md"
    cases = {
        "docker-compose -f docker-compose.yml logs --tail=200 worker-scan | grep x": (
            "logs",
            ("worker-scan",),
        ),
        "docker-compose -f docker-compose.yml logs --tail 50 backend worker-default": (
            "logs",
            ("backend", "worker-default"),
        ),
        "docker-compose logs -t -f backend": ("logs", ("backend",)),
        "docker-compose rm -s -f worker-scan": ("rm", ("worker-scan",)),
        "docker-compose stop -t 30 worker-scan": ("stop", ("worker-scan",)),
        "docker-compose exec -T worker-scan trivy --version": ("exec", ("worker-scan",)),
        "docker-compose run --rm -e A=1 worker-default bash x.sh": ("run", ("worker-default",)),
        "docker-compose -f docker-compose.dev.yml up -d celery-worker": (
            "up",
            ("celery-worker",),
        ),
        "docker-compose --profile engine up -d worker-engine && echo ok": (
            "up",
            ("worker-engine",),
        ),
        "docker-compose up -d --scale worker-scan=3": ("up", ("worker-scan",)),
        "docker-compose up -d --scale=worker-default=2 backend": (
            "up",
            ("worker-default", "backend"),
        ),
        "docker-compose restart <service>": ("restart", ()),
        "docker-compose stop backend worker-scan 2>/dev/null || true": (
            "stop",
            ("backend", "worker-scan"),
        ),
    }
    for line, (subcommand, services) in cases.items():
        (command,) = _parse(page, line)
        assert (command.subcommand, command.services) == (subcommand, services), line
    (dev,) = _parse(page, "docker-compose -f docker-compose.dev.yml logs celery-worker")
    assert dev.compose_files == ("docker-compose.dev.yml",)
    (stray,) = _parse(page, "docker-compose -f docker-compose.prod.yml ps")
    assert stray.unknown_files == ("docker-compose.prod.yml",)
    (layered,) = _parse(
        page, "docker-compose -f docker-compose.yml -f docker-compose.override.yml up -d backend"
    )
    assert layered.unknown_files == () and layered.compose_files == ("docker-compose.yml",)
    assert _parse(page, 'docker-compose -f "$COMPOSE_FILE" up -d worker') == []


def test_every_documented_compose_file_exists() -> None:
    stray = sorted(
        f"{c.page.relative_to(REPO_ROOT)}: {c.text}  (not in the repository: "
        f"{list(c.unknown_files)})"
        for c in COMMANDS
        if c.unknown_files
    )
    assert not stray, f"{len(stray)} command(s) name a compose file that does not exist:\n" + (
        "\n".join(stray)
    )


def test_every_documented_service_exists() -> None:
    stale = []
    for command in COMMANDS:
        services = frozenset().union(*(_services_of(f) for f in command.compose_files))
        unknown = sorted(set(command.services) - services)
        if unknown:
            stale.append(
                f"{command.page.relative_to(REPO_ROOT)}: {command.text}  "
                f"(not in {', '.join(command.compose_files)}: {unknown})"
            )
    assert not stale, f"{len(stale)} documented command(s) name a missing service:\n" + "\n".join(
        stale
    )
