# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Slow down guessing at one account's password.

The existing limiter caps sign-in attempts per IP address. That stops one
machine working through a wordlist and does nothing about the same wordlist
spread over a few hundred addresses, which is the shape credential stuffing
actually takes. So attempts are counted per address as well.

Counting per address is how this kind of control becomes a denial of service:
somebody who knows an email can keep its owner out by getting the password
wrong on purpose, and the better the account the more worthwhile that is. It
cannot be made impossible -- any control that refuses after N failures can be
held open by somebody willing to supply N failures. What it can be made is
expensive and recoverable, and the honest claim is those two, not immunity.

*Expensive*: each successive refusal costs a fresh threshold of failures. The
counter is cleared when a window opens, so the window that follows needs the
whole threshold again rather than a single knock. Attempts arriving inside a
window are refused without being counted at all. And the counter decays on a
clock nobody can push forward: its expiry is set when it is created and never
extended, so quiet time always erases progress.

  This is the part the first version of this module got wrong. It refreshed the
  counter's expiry on every failure and re-opened a window on every failure past
  the threshold, so one wrong password per expired window held an account shut
  for good, at two requests an hour. The rule about not counting inside a window
  is real but it only ever governed the inside of one.

*Recoverable*: finishing a password reset clears the count, which is a way back
that the attacker cannot stand in front of because it needs the inbox. Merely
asking for a reset does not, or anyone could refill their own guessing budget on
demand.

Nothing in the response says whether an address exists. Addresses belonging to
nobody are counted and refused exactly like the rest.

State lives in Redis beside the IP limiter's and expires on its own. Two reasons
it is not a table: counts are keyed by whatever was submitted, including
addresses belonging to no account, and a durable table of those is a disk-filling
attack with extra steps; and losing the counts to a restart costs little, since
this slows guessing rather than being the last line.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import threading
import time
from typing import Any
from weakref import WeakKeyDictionary

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from core.config import (
    login_throttle_enabled,
    login_throttle_failures,
    login_throttle_windows,
    redis_url,
    secret_key,
)
from core.security import normalize_email

log = structlog.get_logger("auth.throttle")

_KEY_PREFIX = "login-throttle:v1:"

#: Purpose tag mixed into the key derivation. ``SECRET_KEY`` also signs JWTs
#: with HS256, which is HMAC-SHA256 over caller-influenced bytes -- the same
#: primitive, the same key. Nothing reachable today turns that into a signing
#: oracle (the message always contains an ``@``, which no base64url JWT input
#: does), but the distance between "not reachable" and "safe" is one refactor
#: that passes a username instead of an address. Deriving a subkey costs one
#: hash and removes the question.
_KEY_INFO = b"trusca/login-throttle/v1"

#: A blocked Redis call sits on the pre-authentication path, so it gets a short
#: leash. Without one, a Redis that drops packets rather than refusing them
#: leaves unauthenticated requests hanging on every worker, which is the
#: held-open request this control exists to avoid handing anybody.
_SOCKET_TIMEOUT_SECONDS = 1.0

# The whole read-modify-write in one round trip, so concurrent failures cannot
# each read "no window open" and then each open one. Done as a script rather
# than MULTI/EXEC because the window length depends on values read inside the
# same operation.
#
# KEYS[1] failure counter   KEYS[2] window marker   KEYS[3] escalation round
# ARGV[1] threshold  ARGV[2] decay seconds  ARGV[3..] window lengths
#
# Returns milliseconds remaining, or 0 to allow the attempt.
_RECORD_FAILURE = """
local ttl = redis.call('PTTL', KEYS[2])
if ttl > 0 then
  return ttl
end

local threshold = tonumber(ARGV[1])
local decay = tonumber(ARGV[2])

local failures = redis.call('INCR', KEYS[1])
if failures == 1 then
  -- Set once, at creation. Refreshing this on every failure is what let a
  -- single wrong password per window keep an account shut for ever.
  redis.call('EXPIRE', KEYS[1], decay)
end

if failures < threshold then
  return 0
end

local round = tonumber(redis.call('GET', KEYS[3]) or '0')
local index = round + 3
if index > #ARGV then
  index = #ARGV
end
local seconds = tonumber(ARGV[index])

redis.call('SET', KEYS[2], '1', 'EX', seconds)
-- The next window costs another full threshold of failures, not one knock.
redis.call('DEL', KEYS[1])
-- The round's expiry IS refreshed here, unlike the counter's, and the two rules
-- differ because the two pieces of state are for different things. The counter
-- decides whether to refuse at all, so it has to forget on a clock nobody can
-- push forward or a stranger's failures accumulate against a person for ever.
-- The round decides how long a refusal lasts, and keeping it high while
-- somebody is still guessing is the point of having it: pinning it to a fixed
-- clock instead would drop a sustained attack back to the first window and take
-- the budget from ten guesses an hour to three hundred. Refreshing it is not
-- free for the attacker either, since reaching this line at all costs a whole
-- threshold of failures, so a refresh is itself evidence the guessing continues.
-- Quiet for `seconds + decay` and the ladder is gone.
redis.call('SET', KEYS[3], round + 1, 'EX', seconds + decay)

return seconds * 1000
"""

#: One client per event loop, and a lock so two threads cannot leave the map
#: and the client disagreeing. A single slot plus a "was it the same loop"
#: comparison was the first version; under two threads with their own loops the
#: two assignments can interleave and leave a client from one loop recorded
#: against the other, after which every call raises ``RuntimeError`` -- which is
#: not a ``RedisError``, so it walks straight past the degrade path and 500s
#: until the process restarts.
_clients: WeakKeyDictionary[asyncio.AbstractEventLoop, Redis] = WeakKeyDictionary()
_clients_lock = threading.Lock()
_loopless_client: Redis | None = None


def _new_client() -> Redis:
    client: Redis = Redis.from_url(
        redis_url(),
        decode_responses=True,
        socket_timeout=_SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
    )
    return client


def _redis() -> Redis:
    """The client for the running loop, made once and kept.

    A connection per call would mean two TCP setups on every sign-in, before any
    credential is checked, and two TLS handshakes where the broker is
    ``rediss://``. So it is kept -- but an asyncio Redis client holds futures
    bound to the loop it was built on and raises "attached to a different loop"
    if it is used from another, which reads like a Redis fault rather than like
    this. Keying by loop means a process that runs several (a test run, a
    threaded worker) gets one each instead of thrashing a single slot, and the
    entries go away with their loops rather than being dropped on the floor for
    ``__del__`` to close against a loop that has since shut.

    ``redis_url()`` is read here rather than captured at import, so the process
    reads the environment at first use like everything else does.
    """
    global _loopless_client
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - only outside a loop
        with _clients_lock:
            if _loopless_client is None:
                _loopless_client = _new_client()
            return _loopless_client

    with _clients_lock:
        client = _clients.get(loop)
        if client is None:
            client = _new_client()
            _clients[loop] = client
        return client


async def close_client() -> None:
    """Drop this loop's client. Called from the app's shutdown."""
    global _loopless_client
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - only outside a loop
        loop = None

    with _clients_lock:
        client = _clients.pop(loop, None) if loop is not None else _loopless_client
        if loop is None:
            _loopless_client = None
    if client is not None:
        await client.aclose()


#: INCR and, on the first attempt only, set the TTL. One round trip so the
#: two cannot come apart: an INCR whose EXPIRE fails leaves a key that never
#: expires, and the key is named after a token id, so that leaks one per
#: attempt for ever.
_SPEND_ATTEMPT = """
local used = redis.call('INCR', KEYS[1])
if used == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return used
"""

#: How many wrong codes one pending token tolerates before it is finished.
#: Three, because a person reading six digits off a phone mistypes, and the
#: code changes every thirty seconds so the honest retry is the common case.
#: Against guessing it is the number that matters: three tries at one in a
#: million per token, on top of the per-address counter.
MFA_ATTEMPTS_PER_TOKEN = 3


#: The in-process floor, used when Redis cannot answer. ``jti`` to
#: ``(attempts used, when the entry expires)``.
_local_attempts: dict[str, tuple[int, float]] = {}
_local_attempts_lock = threading.Lock()


def _local_spend_attempt(jti: str, *, seconds: int) -> bool:
    """The same count, kept in this process, for when Redis cannot answer.

    Per worker, so the effective cap across a deployment is workers times
    :data:`MFA_ATTEMPTS_PER_TOKEN` rather than that number. That is a floor,
    not the guarantee, and it is written here rather than left implicit
    because the difference matters: a six-digit code has no work factor behind
    it, so what stands in front of it during an outage is this and the per-IP
    limiter, and a reader deciding whether that is enough needs the real
    number.

    The first version of this fallback did not exist at all: the counter
    returned "could not answer" and the caller let the exchange through, which
    left a pending token with no cap and no single use for the whole of an
    outage while a comment claimed the opposite.
    """
    now = time.monotonic()
    with _local_attempts_lock:
        # Pruned on the way past rather than on a timer. The map is only
        # written during an outage and each entry lives as long as a pending
        # token, so it stays small; without this it would not.
        for key, (_used, expires) in list(_local_attempts.items()):
            if expires <= now:
                del _local_attempts[key]

        used, expires = _local_attempts.get(jti, (0, now + seconds))
        used += 1
        _local_attempts[jti] = (used, expires)

    return used <= MFA_ATTEMPTS_PER_TOKEN


async def spend_attempt(jti: str, *, seconds: int) -> bool:
    """Count one wrong-code attempt against a pending token.

    ``True`` while the token has attempts left, ``False`` once it is finished.

    Never "could not answer". An unavailable counter used to mean no cap at
    all, which is the wrong way for this control to fail: the code it guards
    is six digits with a one-step drift window either side, so three values in
    a million, and an unlimited number of tries against that inside the
    token's five minutes is not a wall. When Redis cannot answer this falls
    back to :func:`_local_spend_attempt`, which is per worker and therefore
    weaker, but finite.

    The TTL is set once, when the counter is created, and never refreshed, so
    the window cannot be pushed forward by attempting again. That is the same
    mistake the failure counter above made and had to be corrected for.
    """
    key = f"{_KEY_PREFIX}attempts:{jti}"
    try:
        client = _redis()
        # One round trip, and atomic. INCR followed by EXPIRE leaves a key
        # with no TTL if the second call is the one that fails, and the
        # failure counter above already solved this with a script.
        used = await client.eval(  # type: ignore[misc]
            _SPEND_ATTEMPT, 1, key, str(seconds)
        )
    except (RedisError, RuntimeError) as exc:
        _degraded("spend_attempt", exc)
        return _local_spend_attempt(jti, seconds=seconds)
    return int(used) <= MFA_ATTEMPTS_PER_TOKEN


def _keys(email: str) -> tuple[str, str, str]:
    subkey = hmac.new(secret_key().encode("utf-8"), _KEY_INFO, hashlib.sha256).digest()
    digest = hmac.new(subkey, normalize_email(email).encode("utf-8"), hashlib.sha256).hexdigest()
    # Braces make the digest a Redis Cluster hash tag, so the three keys land
    # in one slot and the script can touch all of them. Without it a clustered
    # deployment answers CROSSSLOT, which arrives here as a RedisError and
    # switches the control silently off for every address.
    base = f"{_KEY_PREFIX}{{{digest}}}"
    return base, f"{base}:until", f"{base}:round"


def _decay_seconds() -> int:
    """How long a run of failures is remembered without reaching the threshold.

    The longest window, so a person who mistypes twice on Monday does not meet
    a stranger's three failures from Sunday.
    """
    return max(login_throttle_windows())


def _degraded(action: str, exc: Exception) -> None:
    """Redis is unreachable, or the client is unusable. Say so and carry on.

    Failing the sign-in instead would make Redis a hard dependency of
    authentication for the sake of a control this module's own docstring calls
    a slowdown rather than a last line. The per-IP limiter still applies, and
    the password still has to be right.

    What this catches has to be chosen rather than widened, because the two
    ways of getting it wrong point in opposite directions and both have already
    happened here.

    Too narrow and the process dies on something survivable: a client bound to
    a loop that has closed raises ``RuntimeError``, which is not a
    ``RedisError``, so it used to walk straight past this and 500 the login
    path until a restart. Hence both are caught.

    Too wide and an outage is filed as normal operation: a clustered Redis
    answers CROSSSLOT for a script whose keys land in different slots, and that
    *is* a ``RedisError``, so it arrives here and switches the control silently
    off for every address at once. Nothing in this handler could tell it from a
    Redis that is merely down. That one is not solved by narrowing the catch,
    it is solved by not producing it -- the keys carry a hash tag so they share
    a slot (see ``_keys``).

    Which is the shape of the rule: this handler is for "Redis cannot answer
    right now", and anything that is really a defect in how we talk to Redis
    belongs fixed rather than logged. The warning exists so that the difference
    is visible to somebody reading logs, since the outward behaviour of both is
    the same throttle quietly not running.
    """
    log.warning("auth.throttle_unavailable", action=action, error=str(exc))


async def seconds_until_retry(email: str) -> int:
    """0 when a sign-in may proceed, otherwise the whole seconds left to wait."""
    if not login_throttle_enabled():
        return 0
    try:
        _base, until, _round = _keys(email)
        # PTTL, not TTL: TTL rounds to the nearest second, so a key with 400ms
        # left reports 0 while it still exists. That reads as "allowed", and
        # the attempt it lets through is not counted either -- a free guess at
        # every window boundary, repeatable for as long as anyone cares to poll.
        remaining = int(await _redis().pttl(until))
    except (RedisError, RuntimeError) as exc:
        _degraded("gate", exc)
        return 0
    if remaining <= 0:
        return 0
    return max(1, -(-remaining // 1000))


async def record_failure(email: str) -> int:
    """Count one failed sign-in. Returns the seconds the address is now refused."""
    if not login_throttle_enabled():
        return 0
    windows = login_throttle_windows()
    try:
        base, until, round_key = _keys(email)
        remaining_ms = int(
            await _redis().eval(  # type: ignore[misc]
                _RECORD_FAILURE,
                3,
                base,
                until,
                round_key,
                str(login_throttle_failures()),
                str(_decay_seconds()),
                *[str(w) for w in windows],
            )
        )
    except (RedisError, RuntimeError) as exc:
        _degraded("record_failure", exc)
        return 0

    if remaining_ms <= 0:
        return 0
    seconds = max(1, -(-remaining_ms // 1000))
    log.info("auth.login_throttled", key=_keys(email)[0], seconds=seconds)
    return seconds


async def release_spend(jti: str) -> None:
    """Give back a claim taken by :func:`spend_once` that was not used.

    The claim is taken before the code is checked, so that two requests
    racing with the same token cannot both proceed. A wrong code then has to
    give it back, or one mistyped digit would finish the token: the attempt
    counter is what bounds retries, not this.

    Failures are ignored on purpose. The claim expires with the token either
    way, so the worst a failed release does is cost somebody the rest of that
    token's attempts, which is the same as it working perfectly and them
    running out.
    """
    try:
        await _redis().delete(f"{_KEY_PREFIX}spent:{jti}")
    except (RedisError, RuntimeError) as exc:
        _degraded("release_spend", exc)


async def spend_once(jti: str, *, seconds: int) -> bool:
    """Claim a token id. False if somebody already has it.

    A pending credential is worth one exchange. Leaving it usable after that
    is not exploitable on its own -- whoever holds it still has to supply a
    second factor -- but a value that arrives in a redirect and lives in
    browser history should stop working the moment it has done its job, rather
    than merely stop being useful.

    ``SET NX`` is the whole mechanism: the first caller creates the key and
    gets True, everybody after gets False, and the key expires with the token
    so nothing accumulates.

    Not gated on ``login_throttle_enabled``, unlike the counters above. Single
    use is not a rate: an operator who turns the throttle off is asking for
    failed passwords to stop being counted, and reading that as "and let a
    pending token be exchanged repeatedly" is a decision they did not make.
    That coupling was here first and is the kind that only shows up in an
    incident, when the setting was changed for one reason and took a second
    control with it.

    Redis being unreachable degrades to allowing the exchange. That is a real
    hole and it is not closed here: what bounds it instead is
    :func:`spend_attempt`, which falls back to an in-process counter rather
    than to permitting everything, so a token remains finite during an outage
    even though it stops being single use. Said plainly because the first
    version of this claimed a fallback made the exchange stricter and it did
    not: with Redis down neither control applied at all.
    """
    try:
        created = await _redis().set(f"{_KEY_PREFIX}spent:{jti}", "1", ex=seconds, nx=True)
    except (RedisError, RuntimeError) as exc:
        # Its own line, above the generic one. "Redis is unavailable" tells an
        # operator that something is broken; it does not tell them that for the
        # duration a pending credential can be exchanged more than once. Whoever
        # investigates an incident afterwards needs to know that this specific
        # protection was off during that window, and the generic message does
        # not say which protection.
        log.warning(
            "auth.mfa_single_use_not_enforced",
            reason="redis unavailable",
            window_seconds=seconds,
        )
        _degraded("spend_once", exc)
        return True
    return bool(created)


async def clear(email: str) -> None:
    """Forget an address's failures.

    Called when an authentication has fully succeeded, and when a password
    reset is completed. Never for a reset that was merely requested: anyone can
    request one for any address, and clearing there would let an attacker
    refill their own guessing budget on demand.

    Best effort. It runs after work that has already committed -- a password
    that is already changed, a session that is already issued -- so a Redis
    problem here must not turn a completed action into a failure.
    """
    if not login_throttle_enabled():
        return
    try:
        await _redis().delete(*_keys(email))
    except (RedisError, RuntimeError) as exc:
        _degraded("clear", exc)


def throttle_keys(email: str) -> tuple[str, str, str]:
    """The three Redis keys for an address: counter, window marker, round.

    Public because the admin unlock records the counter key in its audit row,
    so an operator can line the unlock up with the `auth.login_throttled`
    events it answers, and because tests inspect what the script wrote. The
    address itself is never recorded anywhere; the digest is the whole point.
    """
    return _keys(email)


__all__: list[Any] = [
    "MFA_ATTEMPTS_PER_TOKEN",
    "clear",
    "close_client",
    "record_failure",
    "seconds_until_retry",
    "release_spend",
    "spend_attempt",
    "spend_once",
    "throttle_keys",
]
