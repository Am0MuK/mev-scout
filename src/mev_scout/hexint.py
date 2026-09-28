"""Integer parsing for explorer and RPC fields."""

from typing import Any


def parse_int(value: Any) -> int:
    """Parse a hex ("0x1f") or decimal ("31") explorer field.

    Etherscan encodes zero as a bare "0x", which int(..., 0) rejects. Anything
    else that is not a number (including "" and None) still raises ValueError,
    so a missing field is never silently read as 0.
    """
    text = str(value)
    if text in ("0x", "0X"):
        return 0
    try:
        return int(text, 0)
    except ValueError:
        raise ValueError(f"not an integer field: {value!r}") from None
