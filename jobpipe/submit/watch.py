"""watch.py — autonomous local submit watcher (dashboard click → browser).

The dashboard is hosted (Vercel) and cannot open a browser on the user's
machine; it only sets intent by flipping a ``jobs`` row to
``status='prefilling'``. This watcher is the local half: a long-lived process
on the user's MacBook that holds ONE persistent visible browser open and drives
it the instant a row transitions to ``prefilling``.

Transport is **Supabase Realtime** by default — the process idles on a
websocket and acts on click, no busy-polling. A ``--poll`` fallback re-reads the
``prefilling`` queue on an interval for environments where Realtime is
unavailable.

Threading model — the one non-obvious part
-------------------------------------------
Playwright's *sync* API (which the whole prepare flow uses) cannot run inside an
asyncio event loop, and in supabase-py 2.x the **sync** realtime client is a
stub that raises ``NotImplementedError`` — Realtime is async-only. So:

  * the **main thread** owns the sync Playwright context and processes jobs
    strictly one at a time (the human reviews and submits one form at a time);
  * a **daemon thread** runs an ``AsyncRealtimeClient`` event loop whose only
    job is to drop work onto a thread-safe :class:`queue.Queue` — either a job
    id (a row just hit ``prefilling``) or a :data:`_CATCHUP` sentinel ("re-scan
    the queue", emitted on every (re)connect and on each poll tick).

All Supabase reads and all browser work therefore stay on the main thread; the
realtime thread never touches Playwright or makes a blocking DB call. This keeps
the design testable without a real browser, websocket, or network: the event
source and every collaborator are injected.

:class:`SubmitWatcher` is the orchestrator. Its collaborators:

  * ``open_context() -> (context, closer)`` — open the persistent browser once.
  * ``fetch_pending() -> list[dict]`` — rows currently in ``prefilling`` (catch-up).
  * ``fetch_one(job_id) -> dict | None`` — the latest row, re-read at process time.
  * ``process_one(job, context)`` — the existing per-job prepare/fill + stop-and-wait
    advance (blocks until the human flips the row to a terminal decision).
  * ``make_source(enqueue, stop_event) -> source`` — build the event source
    (:class:`RealtimeEventSource` or :class:`PollEventSource`); ``source.start()``
    / ``source.stop()``.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger("submit.watch")

# Queue marker meaning "re-scan the prefilling queue on the main thread". Emitted
# on every realtime (re)connect and on each poll tick, so anything queued while
# the watcher was starting, asleep, or disconnected is picked up. Distinct from a
# job-id (a str) so the consumer can tell them apart.
_CATCHUP = object()


def extract_job_id(payload: object) -> Optional[str]:
    """Pull the row id out of a Realtime ``postgres_changes`` payload.

    realtime-py delivers ``{"data": {"record": {...}, ...}, "ids": [...]}`` for an
    UPDATE. We defend against shape drift (``record`` / ``new`` / a bare row, dict
    or attribute access) and return ``None`` when no id is present.
    """
    def _get(obj, key):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    data = _get(payload, "data")
    for container in (data, payload):
        if container is None:
            continue
        for row_key in ("record", "new"):
            row = _get(container, row_key)
            if row:
                jid = _get(row, "id")
                if jid:
                    return str(jid)
    # Last resort: a bare row payload with an id at the top level.
    jid = _get(payload, "id")
    return str(jid) if jid else None


class WatcherCoordinator:
    """Decide whether THIS machine may claim jobs, and write its heartbeat.

    The dual-machine design (feat/dual-machine-watcher) keeps a watcher running
    on every machine but lets only one — the ``active_watcher_id`` in the
    singleton ``watcher_config`` row — actually claim ``prefilling`` jobs. This
    object is the per-cycle decision, injected into :class:`SubmitWatcher` as its
    ``coordinate`` callback. Each :meth:`should_claim` call does exactly one
    config read + one heartbeat write, so the dormant path stays cheap.

    Collaborators are injected so this is testable without a real database:

      * ``get_active() -> str | None`` — current ``active_watcher_id``.
      * ``record_heartbeat(watcher_id, state)`` — upsert this machine's liveness.
    """

    def __init__(
        self,
        *,
        watcher_id: str,
        get_active: Callable[[], Optional[str]],
        record_heartbeat: Callable[[str, str], None],
    ) -> None:
        self._watcher_id = watcher_id
        self._get_active = get_active
        self._record_heartbeat = record_heartbeat
        # Latch so the "nobody is active" guidance is logged once per unset
        # streak, not every cycle. Reset when a machine is set.
        self._warned_unset = False

    def should_claim(self) -> bool:
        """Return True iff this machine is the active one; write a heartbeat.

        Reads ``active_watcher_id`` first so the heartbeat reflects this cycle's
        decision. A read failure is treated as "dormant" (safer than letting two
        machines act on a transient error) and never raises into the watcher
        loop. When no machine is active, logs a one-time INFO telling the user
        how to pick one, then stays dormant — both machines acting is worse than
        neither.
        """
        try:
            active = self._get_active()
        except Exception:
            logger.exception("active-watcher read failed — staying dormant")
            self._safe_heartbeat("dormant")
            return False

        is_active = active is not None and active == self._watcher_id
        self._safe_heartbeat("active" if is_active else "dormant")

        if active is None:
            if not self._warned_unset:
                logger.info(
                    "No active watcher is set — this machine (%s) is dormant and "
                    "will not claim jobs. Pick one with "
                    "`jobpipe-submit --set-active %s` or from the dashboard.",
                    self._watcher_id, self._watcher_id,
                )
                self._warned_unset = True
            return False

        # A machine is set; allow the unset guidance to fire again if it clears.
        self._warned_unset = False
        if not is_active:
            logger.debug(
                "Dormant — active watcher is %s, this is %s",
                active, self._watcher_id,
            )
        return is_active

    def _safe_heartbeat(self, state: str) -> None:
        try:
            self._record_heartbeat(self._watcher_id, state)
        except Exception:  # a heartbeat write must never kill the watch loop
            logger.exception("heartbeat write failed for %s", self._watcher_id)


class SubmitWatcher:
    """Hold one browser open and drive it when rows transition to ``prefilling``."""

    def __init__(
        self,
        *,
        open_context: Callable[[], Tuple[object, Callable[[], None]]],
        fetch_pending: Callable[[], List[dict]],
        fetch_one: Callable[[str], Optional[dict]],
        process_one: Callable[[dict, object], None],
        make_source: Callable[["_Enqueue", threading.Event], "EventSource"],
        coordinate: Callable[[], bool] | None = None,
        consumer_poll_timeout: float = 1.0,
    ) -> None:
        self._open_context = open_context
        self._fetch_pending = fetch_pending
        self._fetch_one = fetch_one
        self._process_one = process_one
        self._make_source = make_source
        # Dual-machine gate (feat/dual-machine-watcher): returns True when THIS
        # machine is the active one and may claim jobs. It also writes this
        # machine's heartbeat as a side effect, so it must be called once per
        # poll cycle even when dormant. Defaults to "always active" so the
        # single-machine wiring and existing tests need no coordinator.
        self._coordinate = coordinate or (lambda: True)
        self._consumer_poll_timeout = consumer_poll_timeout

        self._queue: "queue.Queue[object]" = queue.Queue()
        # Job ids that are queued-but-not-yet-finished. Guards against the same
        # transition being processed twice when catch-up and a realtime event
        # (or two poll ticks) both observe the same row.
        self._active: set[str] = set()
        self._stop = threading.Event()

    # ── enqueue / dedup ──────────────────────────────────────────────────────
    def _enqueue(self, item: object) -> None:
        """Thread-safe producer entry point handed to the event source.

        ``_CATCHUP`` sentinels are never deduped (each means "re-scan now"); job
        ids are deduped against in-flight work.
        """
        if item is _CATCHUP:
            self._queue.put(_CATCHUP)
            return
        job_id = str(item)
        # set.add is atomic under the GIL; a check-then-add race would at worst
        # enqueue a duplicate id, which the process-time status re-check absorbs.
        if job_id in self._active:
            return
        self._active.add(job_id)
        self._queue.put(job_id)

    def _catch_up(self) -> None:
        """Enqueue every row currently sitting in ``prefilling`` (deduped).

        Runs on every poll tick and realtime (re)connect, so this is where the
        per-cycle heartbeat + active-check live. ``_coordinate()`` writes this
        machine's heartbeat and returns whether it may claim; a dormant machine
        returns here without enqueuing anything (cheap: one config read + one
        heartbeat write, no browser, no claim).
        """
        if not self._coordinate():
            logger.debug("Dormant this cycle — not claiming prefilling jobs")
            return
        try:
            pending = self._fetch_pending() or []
        except Exception:  # never let a transient read kill the loop
            logger.exception("catch-up fetch_pending failed")
            return
        if pending:
            logger.info("Catch-up: %d job(s) in prefilling", len(pending))
        for job in pending:
            jid = job.get("id") if isinstance(job, dict) else None
            if jid:
                self._enqueue(str(jid))

    # ── consume ──────────────────────────────────────────────────────────────
    def _handle_item(self, item: object, context: object) -> None:
        if item is _CATCHUP:
            self._catch_up()
            return
        job_id = str(item)
        try:
            # Active-check gates *claiming* a realtime job event too (poll-mode
            # claims are already gated in _catch_up). A job whose process_one is
            # already running is unaffected — only the claim is gated, so an
            # in-progress stop-and-wait cycle finishes even if the toggle flips.
            if not self._coordinate():
                logger.debug("Dormant — not claiming %s this cycle", job_id)
                return
            job = self._fetch_one(job_id)
            # Re-read at process time: the row may have already advanced (handled
            # by a prior pass, or skipped) between enqueue and now.
            if not job or job.get("status") != "prefilling":
                logger.info("Skipping %s — no longer prefilling", job_id)
                return
            logger.info("Processing prefill for job %s", job_id)
            self._process_one(job, context)
        except Exception:
            logger.exception("process_one failed for %s", job_id)
        finally:
            self._active.discard(job_id)

    def drain(self, context: object) -> int:
        """Process every item currently queued, then return (non-blocking).

        Used by the consumer loop after each blocking ``get`` and directly by
        tests. Strictly sequential — one job at a time.
        """
        handled = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            self._handle_item(item, context)
            handled += 1
        return handled

    def _run_consumer(self, context: object) -> None:
        """Block pulling work off the queue until stopped (main thread)."""
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=self._consumer_poll_timeout)
            except queue.Empty:
                continue
            self._handle_item(item, context)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def request_stop(self) -> None:
        """Signal the consumer loop to exit (e.g. from a SIGINT handler)."""
        self._stop.set()

    def run(self) -> None:
        """Open the browser, drain the startup backlog, then watch until stopped."""
        context, closer = self._open_context()
        source = None
        try:
            # Startup catch-up: process any jobs already in 'prefilling' (queued
            # before the watcher started) BEFORE opening the websocket, so they
            # aren't missed and aren't racing the first realtime event.
            self._catch_up()
            self.drain(context)
            source = self._make_source(self._enqueue, self._stop)
            source.start()
            self._run_consumer(context)
        except KeyboardInterrupt:
            logger.info("Watcher interrupted — shutting down")
        finally:
            self._stop.set()
            if source is not None:
                try:
                    source.stop()
                except Exception:
                    logger.exception("event source stop failed")
            try:
                closer()
            except Exception:
                logger.exception("browser close failed")


# Type alias for the producer callback the watcher hands to a source.
_Enqueue = Callable[[object], None]


class EventSource:
    """Minimal interface: ``start()`` begins producing, ``stop()`` tears down."""

    def start(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class PollEventSource(EventSource):
    """Fallback transport: emit a ``_CATCHUP`` every ``interval`` seconds.

    No websocket — the main thread re-scans ``prefilling`` on each tick. Used by
    ``--poll`` where Supabase Realtime is unavailable.
    """

    def __init__(
        self,
        enqueue: _Enqueue,
        stop_event: threading.Event,
        *,
        interval: float,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._enqueue = enqueue
        self._stop = stop_event
        self._interval = interval
        self._sleep = sleep or __import__("time").sleep
        self._thread: Optional[threading.Thread] = None

    def _tick(self) -> None:
        """One poll cycle — exposed for deterministic testing."""
        self._enqueue(_CATCHUP)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._tick()
            # Sleep in small slices so stop() is honoured promptly.
            waited = 0.0
            step = min(0.5, self._interval) or 0.5
            while waited < self._interval and not self._stop.is_set():
                self._sleep(step)
                waited += step

    def start(self) -> None:
        logger.info("Watcher transport: poll every %ss", self._interval)
        self._thread = threading.Thread(
            target=self._loop, name="submit-watch-poll", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


class RealtimeEventSource(EventSource):
    """Default transport: Supabase Realtime ``postgres_changes`` on ``public.jobs``.

    Runs an ``AsyncRealtimeClient`` event loop on a daemon thread. On every
    (re)connect it emits a ``_CATCHUP`` so anything queued during a gap is swept
    up; on each UPDATE that lands a row in ``prefilling`` it emits that job id.
    Reconnects with exponential backoff; honours ``stop_event``.

    The async machinery is intentionally lazy-imported and lives entirely on the
    background thread — tests inject a fake source instead of exercising it.
    """

    CHANNEL = "jobpipe-submit-watch"

    def __init__(
        self,
        enqueue: _Enqueue,
        stop_event: threading.Event,
        *,
        url: str,
        token: str,
        reconnect_min: float = 1.0,
        reconnect_max: float = 30.0,
    ) -> None:
        self._enqueue = enqueue
        self._stop = stop_event
        self._url = url
        self._token = token
        self._reconnect_min = reconnect_min
        self._reconnect_max = reconnect_max
        self._thread: Optional[threading.Thread] = None

    def _on_change(self, payload: object) -> None:
        jid = extract_job_id(payload)
        if jid:
            logger.info("Realtime: job %s → prefilling", jid)
            self._enqueue(jid)

    def start(self) -> None:
        logger.info("Watcher transport: Supabase Realtime on public.jobs")
        self._thread = threading.Thread(
            target=self._serve_forever, name="submit-watch-realtime", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # The reconnect loop runs on the daemon thread; not covered by unit tests
    # (no live websocket), kept small and side-effect-only.
    def _serve_forever(self) -> None:  # pragma: no cover - requires live network
        import asyncio

        backoff = self._reconnect_min
        while not self._stop.is_set():
            try:
                asyncio.run(self._serve_once())
                # Clean exit (close()) → reset backoff before any retry.
                backoff = self._reconnect_min
            except Exception as exc:
                logger.warning("Realtime connection dropped: %s", exc)
            if self._stop.is_set():
                break
            logger.info("Reconnecting realtime in %.1fs", backoff)
            self._sleep_interruptible(backoff)
            backoff = min(backoff * 2, self._reconnect_max)

    def _sleep_interruptible(self, seconds: float) -> None:  # pragma: no cover
        import time

        waited = 0.0
        while waited < seconds and not self._stop.is_set():
            time.sleep(min(0.5, seconds))
            waited += 0.5

    async def _serve_once(self) -> None:  # pragma: no cover - requires live network
        import asyncio

        from realtime import AsyncRealtimeClient, RealtimeSubscribeStates

        client = AsyncRealtimeClient(self._url, self._token, auto_reconnect=False)
        await client.connect()
        channel = client.channel(self.CHANNEL)
        channel.on_postgres_changes(
            "UPDATE",
            callback=self._on_change,
            schema="public",
            table="jobs",
            filter="status=eq.prefilling",
        )

        def _on_subscribe(state, err=None):
            if state == RealtimeSubscribeStates.SUBSCRIBED:
                # Connected — sweep up anything queued while we were away.
                self._enqueue(_CATCHUP)

        await channel.subscribe(_on_subscribe)
        # Listen until told to stop; poll the stop flag so close() is prompt.
        listen_task = asyncio.ensure_future(client.listen())
        try:
            while not self._stop.is_set() and not listen_task.done():
                await asyncio.sleep(0.5)
        finally:
            listen_task.cancel()
            try:
                await client.close()
            except Exception:
                pass
