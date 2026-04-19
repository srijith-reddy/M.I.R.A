from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Any

from mira.obs.logging import log_event
from mira.runtime.bus import bus
from mira.runtime.orchestrator import run_turn
from mira.runtime.schemas import AgentStatus
from mira.runtime.session import record_turn
from mira.voice.chimes import play_end_chime, play_start_chime
from mira.voice.recorder import record_until_silence
from mira.voice.speech_monitor import SpeechMonitor
from mira.voice.stt import stt
from mira.voice.tts import tts
from mira.voice.wakeword import WakeDetector, make_wakeword


def _tee_stream(stream: Any) -> tuple[Any, list[str]]:
    """Wrap a token iterator so we can both feed TTS live AND collect the
    full text for logging. Returns (iterator_for_tts, collected_buffer).
    After TTS finishes consuming, `"".join(collected_buffer)` is the spoken
    text — that's what record_turn / turn.completed need to persist."""
    collected: list[str] = []

    async def _tee() -> Any:
        async for delta in stream:
            if delta:
                collected.append(delta)
                yield delta

    return _tee(), collected


def _reply_expects_answer(result: Any) -> bool:
    """True when MIRA's reply implies the user should answer next.

    Two signals: an explicit `NEED_CLARIFICATION` status, or a reply that
    ends with a question mark. We trigger an auto-followup in either case
    so the user doesn't need to say the wake word again."""
    if getattr(result, "status", None) == AgentStatus.NEED_CLARIFICATION:
        return True
    reply = (getattr(result, "reply", "") or "").strip()
    return reply.endswith("?")


class VoiceLoop:
    """The daemon heartbeat. One wake → one turn → back to listening.

    Threading layout:
      * Porcupine runs in its own daemon thread (from voice/wakeword.py).
      * `on_wake` is therefore called off the asyncio loop, so we schedule
        the pipeline with `run_coroutine_threadsafe`.
      * A non-blocking busy lock guards against re-entry: if a wake fires
        while a turn is in-flight, we skip it rather than queue. Queuing
        would just let commands pile up during a long browser task — the
        user will prefer immediate feedback ("still busy") or an interrupt.

    State reporting: publishes `ui.state` events (`idle`, `listening`,
    `thinking`, `speaking`) on the bus so the menu-bar / HUD can reflect
    them without being coupled to the pipeline internals.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._wake: WakeDetector | None = None
        self._busy = threading.Lock()
        # Reminders that fired while a turn was running. Spoken on the next
        # idle transition so they aren't silently swallowed by the scheduler
        # (which has already marked them done on fire).
        self._pending_reminders: deque[dict[str, Any]] = deque()
        self._reminder_unsub = None

    def start(self) -> None:
        def _on_wake() -> None:
            if not self._busy.acquire(blocking=False):
                log_event("voice.busy_skip")
                return
            fut = asyncio.run_coroutine_threadsafe(self._pipeline(), self._loop)
            fut.add_done_callback(lambda _f: self._busy.release())

        self._wake = make_wakeword(on_wake=_on_wake)
        self._wake.start()
        self._reminder_unsub = bus().subscribe("reminder.fired", self._on_reminder_fired)
        bus().publish_nowait("ui.state", state="idle")
        # Kick off TTS cache prewarm in the background. First run pays
        # ~$0.01 of Cartesia to synthesize the stock phrases; subsequent
        # runs are no-ops (skipped per-phrase if already cached). Runs
        # off the hot path so boot is never blocked on network.
        asyncio.run_coroutine_threadsafe(_prewarm_tts_cache(), self._loop)
        log_event("voice.loop_started")

    def stop(self) -> None:
        if self._wake is not None:
            self._wake.stop()
            self._wake = None
        if self._reminder_unsub is not None:
            self._reminder_unsub()
            self._reminder_unsub = None
        log_event("voice.loop_stopped")

    async def _on_reminder_fired(self, topic: str, payload: dict[str, Any]) -> None:
        # If a turn is running we can't grab the mic/speaker without stepping
        # on it, so queue and let the pipeline drain on its way back to idle.
        # If idle, speak immediately.
        if not self._busy.acquire(blocking=False):
            self._pending_reminders.append(payload)
            log_event("voice.reminder_queued", id=payload.get("id"))
            return
        try:
            await self._speak_reminder(payload)
        finally:
            self._busy.release()
        bus().publish_nowait("ui.state", state="idle")

    async def _speak_reminder(self, payload: dict[str, Any]) -> None:
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        bus().publish_nowait("ui.state", state="speaking")
        if self._wake is not None:
            self._wake.pause()
        try:
            await tts().speak(f"Reminder: {text}")
        except Exception as exc:
            log_event("voice.reminder_speak_error", error=repr(exc))
        finally:
            if self._wake is not None:
                self._wake.resume()

    async def _speak_stream_and_persist(
        self, result: Any, transcript_text: str
    ) -> None:
        """Speak a token-stream reply and persist its side effects after.

        The orchestrator deliberately skips publish + record_turn for
        streaming turns because the final text isn't known until playback
        finishes. We tee the iterator through `_tee_stream`, drain it into
        TTS, then publish turn.completed and record_turn with the joined
        text. Caching is skipped — streaming reply paths are research-only
        today, which is volatile by definition."""
        stream, collected = _tee_stream(result.reply_stream)
        try:
            await tts().speak_stream(stream)
        except Exception as exc:
            log_event("voice.stream_error", error=repr(exc))

        final_text = "".join(collected).strip()
        try:
            await bus().publish(
                "turn.completed",
                turn_id=result.turn_id,
                transcript=transcript_text,
                reply=final_text,
                status=result.status.value,
                via=result.via,
            )
            record_turn(
                turn_id=result.turn_id,
                transcript=transcript_text,
                reply=final_text,
                status=result.status.value,
                via=result.via,
                user_id="local",
            )
        except Exception as exc:
            log_event("voice.stream_persist_error", error=repr(exc))

    async def _drain_pending_reminders(self) -> None:
        while self._pending_reminders:
            payload = self._pending_reminders.popleft()
            await self._speak_reminder(payload)

    async def _pipeline(self) -> None:
        try:
            bus().publish_nowait("ui.state", state="listening")

            capture = await asyncio.to_thread(record_until_silence)
            if capture.aborted or capture.pcm.size == 0:
                return

            bus().publish_nowait("ui.state", state="thinking")
            transcript = await stt().transcribe(capture.pcm, capture.sample_rate)
            if not transcript.text.strip():
                return
            log_event(
                "voice.transcript",
                text=transcript.text[:160],
                provider=transcript.provider,
                latency_ms=transcript.latency_ms,
            )

            result = await run_turn(transcript.text)

            if (result.reply_stream is not None or result.reply) and not result.silent:
                bus().publish_nowait("ui.state", state="speaking")
                # Mute the wake listener during playback so speaker audio
                # can't ring the keyword. A SpeechMonitor takes the mic in
                # parallel so barge-in still works — first voiced frames
                # publish `speech.started`, which TTS subscribes to and uses
                # to cancel playback.
                if self._wake is not None:
                    self._wake.pause()
                monitor = SpeechMonitor()
                monitor.start()
                try:
                    if result.reply_stream is not None:
                        await self._speak_stream_and_persist(
                            result, transcript.text
                        )
                    else:
                        await tts().speak(result.reply)  # type: ignore[arg-type]
                finally:
                    monitor.stop()
                    if self._wake is not None:
                        self._wake.resume()
                if monitor.fired():
                    # The user talked over us — immediately start a fresh
                    # turn instead of lapsing back to idle. We re-enter the
                    # pipeline body inline rather than recursing, to avoid
                    # re-acquiring the busy lock.
                    log_event("voice.barge_in_followup")
                    await self._barge_in_followup()
                elif _reply_expects_answer(result):
                    # MIRA asked a question — stay open for the user's reply
                    # without requiring another wake word. Reuses the
                    # follow-up pipeline so the continuation runs through
                    # the same memory-aware path.
                    log_event("voice.auto_followup", kind="clarify")
                    await self._barge_in_followup()
        except Exception as exc:
            log_event("voice.loop_error", error=repr(exc))
            # Never die on a single bad turn. Try to apologize, then go idle.
            try:
                if self._wake is not None:
                    self._wake.pause()
                await tts().speak("Something went wrong. Try again.")
            except Exception:
                pass
            finally:
                if self._wake is not None:
                    self._wake.resume()
        finally:
            try:
                await self._drain_pending_reminders()
            except Exception as exc:
                log_event("voice.reminder_drain_error", error=repr(exc))
            bus().publish_nowait("ui.state", state="idle")

    # ---------- HUD command surface ----------

    async def handle_ui_command(self, cmd: str, data: dict[str, Any]) -> None:
        """Entry point for commands from the SwiftUI HUD via `ui_bridge`.

        The bridge schedules us as a coroutine on the asyncio loop, so we
        can await TTS/pipeline work directly. Each command is intentionally
        tiny — if a command sprouts logic, move it into the pipeline and
        call that instead.
        """
        if cmd == "cmd.stop":
            # Interrupt any in-flight TTS. `tts.cancel()` sets the async
            # cancel event that speak_stream polls, so playback stops
            # within one audio buffer. If nothing is speaking this is a
            # no-op — safe to call eagerly.
            try:
                tts().cancel()
            except Exception as exc:
                log_event("voice.cmd_stop_error", error=repr(exc))
        elif cmd == "cmd.barge_in":
            # Same effect as the user speaking: publish `speech.started`,
            # which TTS already subscribes to for barge-in cancellation.
            # Reusing the existing bus topic keeps one code path — we
            # don't want two subtly different cancel semantics.
            bus().publish_nowait("speech.started")
        elif cmd == "cmd.submit_text":
            text = str(data.get("text") or "").strip()
            if not text:
                return
            await self._text_turn(text)

    async def _text_turn(self, text: str) -> None:
        """Run a turn from typed input instead of spoken input.

        Skips record + STT; everything downstream is identical so the HUD
        still gets supervisor/agent/tool events, and the reply is spoken
        just like a voice turn.
        """
        if not self._busy.acquire(blocking=False):
            log_event("voice.text_busy_skip", text=text[:80])
            return
        try:
            bus().publish_nowait("ui.state", state="thinking")
            log_event("voice.text_input", text=text[:200])
            result = await run_turn(text)
            if (result.reply_stream is not None or result.reply) and not result.silent:
                bus().publish_nowait("ui.state", state="speaking")
                if self._wake is not None:
                    self._wake.pause()
                try:
                    if result.reply_stream is not None:
                        await self._speak_stream_and_persist(result, text)
                    else:
                        await tts().speak(result.reply)  # type: ignore[arg-type]
                finally:
                    if self._wake is not None:
                        self._wake.resume()
        except Exception as exc:
            log_event("voice.text_turn_error", error=repr(exc))
        finally:
            bus().publish_nowait("ui.state", state="idle")
            self._busy.release()

    async def _barge_in_followup(self) -> None:
        """Inline second turn after a barge-in cancel.

        We skip the start-chime — the user is already speaking, and playing
        audio at them mid-utterance is jarring. Everything else mirrors the
        normal pipeline."""
        try:
            bus().publish_nowait("ui.state", state="listening")
            capture = await asyncio.to_thread(record_until_silence)
            if capture.aborted or capture.pcm.size == 0:
                return
            bus().publish_nowait("ui.state", state="thinking")
            transcript = await stt().transcribe(capture.pcm, capture.sample_rate)
            if not transcript.text.strip():
                return
            log_event(
                "voice.followup_transcript",
                text=transcript.text[:160],
                provider=transcript.provider,
            )
            result = await run_turn(transcript.text)
            if (result.reply_stream is not None or result.reply) and not result.silent:
                bus().publish_nowait("ui.state", state="speaking")
                if self._wake is not None:
                    self._wake.pause()
                monitor = SpeechMonitor()
                monitor.start()
                try:
                    if result.reply_stream is not None:
                        await self._speak_stream_and_persist(
                            result, transcript.text
                        )
                    else:
                        await tts().speak(result.reply)  # type: ignore[arg-type]
                finally:
                    monitor.stop()
                    if self._wake is not None:
                        self._wake.resume()
        except Exception as exc:
            log_event("voice.followup_error", error=repr(exc))


async def _prewarm_tts_cache() -> None:
    """Populate the TTS cache with stock phrases the daemon emits every
    turn ("Done.", "Got it.", etc.). First boot pays the Cartesia cost
    once; from then on those utterances are served off disk for free.

    Kept defensive — any failure here is logged and dropped. A cache
    miss at runtime just means we synthesize live, which is the status
    quo anyway."""
    from mira.config.settings import get_settings
    from mira.voice import tts_cache

    try:
        settings = get_settings()
        voice_id = settings.cartesia_voice
        model = settings.cartesia_tts_model
        if not voice_id or not model:
            log_event("tts_cache.prewarm_skipped", reason="no_voice_config")
            return
        # Evict first so a stale cache from a prior voice/model doesn't
        # starve the new entries out of the size budget.
        tts_cache.sweep()

        client = tts().__class__._get_client(tts())  # reuse the lazy client
        import numpy as np

        def _synth(phrase: str):
            out: list = []
            for chunk in client.tts.bytes(
                model_id=model,
                transcript=phrase,
                voice={"id": voice_id},
                output_format={
                    "container": "raw",
                    "encoding": "pcm_f32le",
                    "sample_rate": 24000,
                },
            ):
                out.append(np.frombuffer(chunk, dtype=np.float32))
            return np.concatenate(out) if out else None

        written = await asyncio.to_thread(
            tts_cache.prewarm_if_empty,
            voice_id=voice_id,
            model=model,
            synthesize=_synth,
        )
        log_event("tts_cache.prewarm_done", written=written)
    except Exception as exc:
        log_event("tts_cache.prewarm_error", error=repr(exc))

