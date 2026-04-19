from __future__ import annotations

import argparse
import asyncio
import sys

from mira import __version__
from mira.config.paths import paths
from mira.config.settings import get_settings
from mira.obs.logging import get_logger, log_event, setup_logging


def _boot() -> None:
    paths.ensure()
    setup_logging()
    settings = get_settings()
    log_event(
        "boot",
        version=__version__,
        user=settings.user_name,
        data_dir=str(paths.data_dir),
        config_dir=str(paths.config_dir),
        cache_dir=str(paths.cache_dir),
        logs_dir=str(paths.logs_dir),
    )
    missing = settings.missing_required_keys()
    if missing:
        log_event("config.missing_keys", keys=missing)

    _seed_profile(settings)


def _seed_profile(settings) -> None:  # noqa: ANN001
    """Push durable facts from .env into the memory profile on boot.

    Only seeds keys that aren't already present — if MIRA learned a better
    value during conversation (e.g. "actually call me Shreyansh"), that
    wins over whatever's in .env. Env is the fallback, memory is truth.
    """
    try:
        from mira.runtime.memory import memory

        mem = memory()
        if settings.user_name and settings.user_name != "friend":
            if not mem.get_profile("user_name"):
                mem.set_profile("user_name", settings.user_name)
    except Exception as exc:
        log_event("boot.seed_profile_error", error=repr(exc))


async def _text_turn(transcript: str) -> int:
    from mira.runtime.orchestrator import run_turn

    result = await run_turn(transcript)
    if result.reply:
        prefix = f"[{result.status.value}] " if result.status.value != "done" else ""
        print(prefix + result.reply)
    else:
        print(f"[{result.status.value}] {result.error or '(no reply)'}", file=sys.stderr)
    return 0 if result.status.value in ("done", "need_confirmation", "need_clarification") else 1


def _diag() -> int:
    logger = get_logger("mira.boot")
    logger.info("MIRA v2 online (Batch 6).")
    logger.info("  data:   %s", paths.data_dir)
    logger.info("  config: %s", paths.config_dir)
    logger.info("  logs:   %s", paths.logs_dir)
    settings = get_settings()
    missing = settings.missing_required_keys()
    if missing:
        logger.warning("Missing keys: %s", ", ".join(missing))
    return 0


def _run_daemon() -> int:
    # Import lazily so `text` and diag paths don't pay rumps' AppKit bind cost.
    from mira.ui.menubar import run_app

    log_event("daemon.starting")
    run_app()
    return 0


def _run_doctor() -> int:
    from mira.diagnostics import exit_code, format_report, run_all

    checks = run_all()
    print("MIRA doctor:")
    print(format_report(checks))
    return exit_code(checks)


def _run_setup() -> int:
    from mira.install.wizard import run_wizard

    return run_wizard()


def _run_install() -> int:
    from mira.install.launchd import install

    return install()


def _run_uninstall() -> int:
    from mira.install.launchd import uninstall

    return uninstall()


def _run_status() -> int:
    from mira.install.launchd import status

    return status()


def _run_turns(limit: int) -> int:
    """Print the most recent N turns as a compact table."""
    from mira.obs.dashboard import _recent_turns

    rows = _recent_turns(limit)
    if not rows:
        print("(no turns recorded yet)")
        return 0

    import datetime as _dt

    print(f"{'when':<10} {'via':<22} {'status':<8} {'ms':>8} {'cost':>9}  transcript")
    print("-" * 100)
    for r in rows:
        ts = r.get("ended_at") or r.get("started_at") or 0
        when = _dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "—"
        via = (r.get("via") or "—")[:22]
        status = (r.get("status") or "—")[:8]
        ms = int(r["latency_ms"]) if r.get("latency_ms") is not None else 0
        cost = f"${r['cost_usd']:.4f}" if r.get("cost_usd") else "—"
        transcript = (r.get("transcript") or "")[:60]
        print(f"{when:<10} {via:<22} {status:<8} {ms:>8} {cost:>9}  {transcript}")
    return 0


def _run_stats() -> int:
    from mira.obs.dashboard import _stats_24h

    s = _stats_24h()
    print("MIRA — last 24h:")
    print(f"  turns:       {s['turns_24h']}")
    print(f"  errors:      {s['errors_24h']}")
    print(f"  cost:        ${s['cost_usd_24h']:.4f}")
    p50 = s.get("p50_latency_ms")
    p95 = s.get("p95_latency_ms")
    print(f"  p50 latency: {p50 if p50 is not None else '—'} ms")
    print(f"  p95 latency: {p95 if p95 is not None else '—'} ms")
    return 0


def _run_reembed() -> int:
    from mira.runtime.memory import memory

    mem = memory()
    active = mem._active_embed_model()
    print(f"Re-embedding stale episodes with {active}...")
    counts = mem.reembed_stale()
    print(
        f"  scanned:    {counts['scanned']}\n"
        f"  reembedded: {counts['reembedded']}\n"
        f"  skipped:    {counts['skipped']}\n"
        f"  errors:     {counts['errors']}"
    )
    return 0 if counts["errors"] == 0 else 1


def _run_dashboard(port: int | None) -> int:
    from mira.obs.dashboard import serve_blocking

    return serve_blocking(port=port)


def _build_parser() -> argparse.ArgumentParser:
    """Subcommand layout: each verb is a separate sub-parser so help is
    scoped (e.g. `mira turns --limit 50`). The bare `mira` call still prints
    the diag banner."""
    parser = argparse.ArgumentParser(prog="mira")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("daemon", help="Launch the menu-bar daemon (wake word + voice pipeline).")
    sub.add_parser("doctor", help="Run diagnostics: keys, deps, audio, sqlite.")
    sub.add_parser("setup", help="Run the interactive first-run wizard.")
    sub.add_parser("install", help="Install the LaunchAgent so MIRA starts at login.")
    sub.add_parser("uninstall", help="Remove the LaunchAgent.")
    sub.add_parser("status", help="Report whether the LaunchAgent is loaded.")
    sub.add_parser("stats", help="Print aggregate stats for the last 24h.")
    sub.add_parser("reembed", help="Rebuild embeddings for episodes under a different embedder.")

    p_text = sub.add_parser("text", help="Run one text-mode turn and print the reply.")
    p_text.add_argument("prompt", type=str)

    p_turns = sub.add_parser("turns", help="Print the most recent turns.")
    p_turns.add_argument("--limit", type=int, default=20)

    p_dash = sub.add_parser("dashboard", help="Run the local telemetry dashboard in the foreground.")
    p_dash.add_argument("--port", type=int, default=None)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    cmd = args.cmd

    # Install/setup paths skip full boot — they run before keys exist.
    if cmd == "setup":
        return _run_setup()
    if cmd == "install":
        return _run_install()
    if cmd == "uninstall":
        return _run_uninstall()
    if cmd == "status":
        return _run_status()

    _boot()

    if cmd == "text":
        return asyncio.run(_text_turn(args.prompt))
    if cmd == "daemon":
        return _run_daemon()
    if cmd == "doctor":
        return _run_doctor()
    if cmd == "turns":
        return _run_turns(args.limit)
    if cmd == "stats":
        return _run_stats()
    if cmd == "reembed":
        return _run_reembed()
    if cmd == "dashboard":
        return _run_dashboard(args.port)
    return _diag()


if __name__ == "__main__":
    raise SystemExit(main())
