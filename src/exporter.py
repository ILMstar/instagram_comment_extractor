"""Export module for formatting and persisting extracted usernames.

Ensures strict LF line endings (\n), preserves occurrence order and duplicates,
omits any '@' prefix, and produces no trailing blank line.
"""

from pathlib import Path
from typing import Sequence


def format_usernames_payload(usernames: Sequence[str]) -> str:
    """Joins usernames with '\\n' with no trailing newline."""
    cleaned = [u.lstrip("@") for u in usernames]
    return "\n".join(cleaned)


def write_usernames_file(
    shortcode: str,
    usernames: Sequence[str],
    output_dir: Path,
) -> Path:
    """Writes <shortcode>_usernames.txt with strict LF endings and returns the file path."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    target_path = output_dir / f"{shortcode}_usernames.txt"
    payload = format_usernames_payload(usernames)

    with open(target_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(payload)

    return target_path

