from __future__ import annotations

import unicodedata


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def normalize_text(value: str) -> str:
    """Normalize Chinese and English text with one deterministic policy.

    Compatibility normalization folds full-width Latin characters and
    punctuation. Letter casing is folded, punctuation/symbols become token
    boundaries, and repeated whitespace is collapsed.
    """

    normalized = unicodedata.normalize("NFKC", value).casefold()
    characters: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category[0] in {"P", "S", "Z", "C"}:
            characters.append(" ")
        else:
            characters.append(character)
    return " ".join("".join(characters).split())


def tokenize(value: str) -> list[str]:
    """Tokenize normalized mixed Chinese/English text without global state.

    Han characters are individual tokens; contiguous non-Han letters and
    numbers form word tokens. The same function is used for indexing and
    querying, avoiding environment-dependent tokenizer drift.
    """

    normalized = normalize_text(value)
    tokens: list[str] = []
    word: list[str] = []

    def flush_word() -> None:
        if word:
            tokens.append("".join(word))
            word.clear()

    for character in normalized:
        if character.isspace():
            flush_word()
        elif _is_cjk(character):
            flush_word()
            tokens.append(character)
        elif character.isalnum() or unicodedata.category(character).startswith("M"):
            word.append(character)
        else:
            flush_word()
    flush_word()
    return tokens
