"""Install-time helpers: first-run setup, launchd LaunchAgent management.

Kept separate from runtime modules so a bare `import mira` in the daemon
doesn't pay the cost of pulling these in — they're only touched by the
CLI install paths.
"""
