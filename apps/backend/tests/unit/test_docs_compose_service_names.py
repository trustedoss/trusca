"""Every compose service a documented command names exists in that compose file.

The production stack split the single ``worker`` service into ``worker-scan``,
``worker-default`` and ``worker-engine``, and about sixty commands in the
guides (EN and KO) kept naming ``worker`` (#582). ``docker-compose exec
worker ...`` then fails with ``No such service: worker``, which is the first
thing an operator sees in the middle of an incident.

The check reads every ``docker-compose`` command in the guides' code (fenced
blocks and inline code spans) and in the README, works out which compose file
it targets from its ``-f`` options (``docker-compose.yml`` when there is none),
and requires each service argument of a service-taking subcommand to be a
service of that file. Prose such as "restart the worker" is not a command and
is not checked.
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
EXTRA_PAGES = (REPO_ROOT / "README.md",)

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

#: Subcommands whose positional arguments are all service names.
_MULTI_SERVICE = frozenset(
    {
        "logs",
        "restart",
        "stop",
        "start",
        "up",
        "ps",
        "kill",
        "rm",
        "pull",
        "top",
        "pause",
        "unpause",
        "create",
        "build",
        "events",
    }
)
#: Subcommands whose first positional argument is the service and the rest a
#: command run inside it.
_SINGLE_SERVICE = frozenset({"exec", "run", "port"})

#: Subcommand options that take a value, for the subcommands above.
_SUBCOMMAND_VALUE_OPTIONS = frozenset(
    {
        "--tail",
        "--since",
        "--until",
        "-t",
        "--timeout",
        "--scale",
        "-e",
        "--env",
        "-u",
        "--user",
        "-w",
        "--workdir",
        "--index",
        "--name",
        "--entrypoint",
        "-v",
        "--volume",
        "-p",
        "--publish",
        "-l",
        "--label",
        "--protocol",
        "--build-arg",
        "-s",
        "--signal",
        "--filter",
    }
)

#: Shell tokens that end a command.
_OPERATOR_CHARS = "|;&<>()"
_FENCE = re.compile(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", re.M | re.S)
_INLINE = re.compile(r"(`+)(.+?)\1", re.S)


@dataclass(frozen=True)
class Command:
    page: pathlib.Path
    text: str
    compose_files: tuple[str, ...]
    subcommand: str
    services: tuple[str, ...]


def _services_of(name: str) -> frozenset[str]:
    document = yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8")) or {}
    return frozenset(document.get("services") or {})


def _pages() -> list[pathlib.Path]:
    pages = [
        path for root in DOCS_ROOTS for path in sorted(root.rglob("*.md*")) if path.is_file()
    ]
    return pages + [path for path in EXTRA_PAGES if path.is_file()]


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
                break
            words.append(word)
    except ValueError:
        # An unclosed quote: the line continues past this fragment. Keep the
        # words read so far, they hold the subcommand and the service.
        pass
    return words


def _parse(page: pathlib.Path, line: str) -> list[Command]:
    commands = []
    for start in (m.end() for m in re.finditer(r"\bdocker-compose\b", line)):
        tokens = _tokens(line[start:])
        files: list[str] = []
        index = 0
        while index < len(tokens) and tokens[index].startswith("-"):
            option = tokens[index]
            name, _, inline = option.partition("=")
            if name in _GLOBAL_VALUE_OPTIONS:
                value = inline or (tokens[index + 1] if index + 1 < len(tokens) else "")
                if name in {"-f", "--file"}:
                    files.append(pathlib.PurePosixPath(value).name)
                index += 1 if inline else 2
            else:
                index += 1
        if index >= len(tokens):
            continue
        subcommand = tokens[index]
        if subcommand not in _MULTI_SERVICE | _SINGLE_SERVICE:
            continue
        positionals: list[str] = []
        index += 1
        while index < len(tokens):
            token = tokens[index]
            if token.startswith("-"):
                name, _, inline = token.partition("=")
                index += 2 if name in _SUBCOMMAND_VALUE_OPTIONS and not inline else 1
                continue
            positionals.append(token)
            if subcommand in _SINGLE_SERVICE:
                # The rest is the command run inside the service.
                break
            index += 1
        known = tuple(f for f in files if (REPO_ROOT / f).is_file())
        commands.append(
            Command(
                page=page,
                text=" ".join(["docker-compose", *tokens]),
                compose_files=known or (COMPOSE_PROD.name,),
                subcommand=subcommand,
                services=tuple(p for p in positionals if not _is_placeholder(p)),
            )
        )
    return commands


def _commands() -> list[Command]:
    found = []
    for page in _pages():
        for fragment in _code_fragments(page.read_text(encoding="utf-8")):
            for line in fragment.splitlines():
                found += _parse(page, line)
    return found


COMMANDS = _commands()


def test_the_scan_finds_the_commands_it_is_about() -> None:
    """Not vacuous: the guides name services in well over a hundred commands,
    on both locales, in the exec, logs and restart forms."""
    with_services = [c for c in COMMANDS if c.services]
    assert len(with_services) > 100, f"only {len(with_services)} commands found"
    assert any("i18n" in c.page.parts for c in with_services), "no KO command found"
    assert {"exec", "logs", "restart"} <= {c.subcommand for c in with_services}
    assert any(
        c.compose_files == ("docker-compose.dev.yml",) for c in with_services
    ), "no dev-stack command found, so the -f handling is not exercised"
    # The private-CA page's log command, which an earlier, narrower check on
    # that page alone used to cover.
    assert any(
        c.page.name == "private-ca.md" and c.subcommand == "logs" for c in with_services
    ), "the private-CA log command was not found"


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
        "docker-compose restart <service>": ("restart", ()),
    }
    for line, (subcommand, services) in cases.items():
        (command,) = _parse(page, line)
        assert (command.subcommand, command.services) == (subcommand, services), line
    (dev,) = _parse(page, "docker-compose -f docker-compose.dev.yml logs celery-worker")
    assert dev.compose_files == ("docker-compose.dev.yml",)


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
