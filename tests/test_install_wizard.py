from __future__ import annotations

import os
from pathlib import Path

from mira.install.wizard import _parse_env_file, _write_env_file


def test_parse_env_empty_for_missing_file(tmp_path: Path) -> None:
    assert _parse_env_file(tmp_path / "nope.env") == {}


def test_parse_env_handles_quotes_and_comments(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text(
        "# header comment\n"
        "OPENAI_API_KEY=\"sk-abc\"\n"
        "USER_NAME='shrey'\n"
        "\n"
        "BRAVE=plain\n"
        "# trailing\n"
    )
    got = _parse_env_file(p)
    assert got == {
        "OPENAI_API_KEY": "sk-abc",
        "USER_NAME": "shrey",
        "BRAVE": "plain",
    }


def test_write_env_is_chmod_600_and_sorted(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    _write_env_file(target, {"B": "two", "A": "one"})
    # Ordering: keys sorted → deterministic diffs in version control.
    body = target.read_text().splitlines()
    assert body[0].startswith("#")
    assert body[1] == 'A="one"'
    assert body[2] == 'B="two"'

    mode = os.stat(target).st_mode & 0o777
    assert mode == 0o600


def test_write_env_atomic_no_tmp_left_behind(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    _write_env_file(target, {"K": "v"})
    assert target.exists()
    assert not target.with_suffix(".env.tmp").exists()
