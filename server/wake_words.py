"""Wake-word parsing shared by the STT server and lightweight tests."""

from __future__ import annotations

import re
from typing import Pattern


def parse_wake_words(value: str) -> list[str]:
    return [word.strip() for word in value.split(",") if word.strip()]


def compile_wake_pattern(wake_words: list[str]) -> Pattern[str]:
    alternatives: list[str] = []
    for wake_word in sorted(wake_words, key=len, reverse=True):
        words = wake_word.split()
        if words:
            alternatives.append(r"[\W_]+".join(re.escape(word) for word in words))
    if not alternatives:
        raise ValueError("Список wake words пуст")
    return re.compile(
        rf"(?<!\w)(?:{'|'.join(alternatives)})(?!\w)",
        flags=re.IGNORECASE,
    )


def extract_wake_command(text: str, pattern: Pattern[str]) -> tuple[bool, str, str | None]:
    """Remove every wake-word repetition and return the remaining command."""
    first_match = pattern.search(text)
    if first_match is None:
        return False, "", None

    command = pattern.sub(" ", text)
    command = re.sub(r"\s+", " ", command)
    command = re.sub(r"\s+([,.:;!?])", r"\1", command)
    command = command.strip(" \t\r\n,.:;!?—-")
    return True, command, first_match.group(0)
