"""Thin wrappers around external systems (macOS native frameworks, third-party
APIs). Each module keeps its heavy imports lazy so that importing `mira` on
a machine without that system installed never crashes."""
