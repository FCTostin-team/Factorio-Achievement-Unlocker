"""Byte signatures and edits for the two supported binary formats.

These signatures are version dependent. The engine requires every enabled rule
to have its expected number of matches before it produces an output file.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    name: str
    signature: str
    offset: int
    before: bytes
    after: bytes
    count: int = 1
    data_mode: str = "both"


LINUX_RULES = (
    Rule("achievement data selection", "74 2E 48 8D 15 ?? ?? ?? ?? EB 0D 0F 1F 40 00 48 83 C0 08", 0, b"\x74", b"\x75", data_mode="vanilla"),
    Rule("achievement screen refresh", "0F 84 DF 00 00 00 48 8B 10 80 7A 3E 01 75 EA", 1, b"\x84", b"\x85"),
    Rule("Steam stat update", "74 1A 4C 8B 00 41 80 78 3E 01 75 ED 41 80 78 40 01 75 E6 41 80 78 41", 0, b"\x74", b"\xEB"),
    Rule("Steam achievement unlock", "74 17 48 8B 10 80 7A 3E 01 75 EE 80 7A 40 01 75 E8 80 7A 41 01 74 E2 EB 3C", 0, b"\x74", b"\xEB"),
    Rule("Steam stats callback", "74 ?? 48 8B ?? 80 7A ?? ?? ?? ?? 80 7A ?? ?? ?? ?? 80 7A ?? ?? ?? ?? E9 22 01 00 00", 0, b"\x74", b"\xEB"),
    Rule("Steam stats callback alternate", "74 68 48 BA 74 65 73 74 5F 6D 6F 64 EB 0F 66 0F 1F 44 00 00 48 83 C0 08", 0, b"\x74", b"\xEB"),
    Rule("achievement screen gate", "74 07 48 83 78 20 00 75 CC", 0, b"\x74", b"\xEB"),
    Rule("achievement screen gate alternate", "75 CC 49 8B 80 ?? 01", 0, b"\x75", b"\xEB"),
)

WINDOWS_RULES = (
    Rule("achievement eligibility", "75 ?? 80 BF ?? ?? ?? ?? 00 75 ?? 48 8B 9F ?? ?? ?? ?? 48 85 DB 74", 21, b"\x74", b"\xEB"),
    Rule("Steam stat and unlock gates", "48 8B 08 80 79 ?? ?? 74 ?? 80 79 ?? ?? 74 ?? 80 79 ?? ?? 74 ??", 7, b"\x74", b"\xEB", count=2),
    Rule("Steam synchronization gates", "48 8B 02 80 78 ?? ?? 74 ?? 80 78 ?? ?? 74 ?? 80 78 ?? ??", 7, b"\x74", b"\xEB", count=2),
    Rule("Steam stats callback", "8B ?? 08 ?? 3B ?? 74 22 48 8B 01 80 78", 6, b"\x74", b"\x75"),
    Rule("achievement data selection", "48 8D 0D ?? ?? ?? ?? 48 8D 15 ?? ?? ?? ?? 84 C0 48 0F 45 D1 49 8D 8D", 16, b"\x48\x0F\x45", b"\x90\x48\x8B", data_mode="vanilla"),
    Rule("modded data flag", "E8 ?? ?? ?? ?? 34 ?? 41 88 85 ?? ?? ?? ?? 48 8D 94 24", 5, b"\x34", b"\x30\xC0", data_mode="vanilla"),
)


def rules_for(format_name: str, data_mode: str) -> tuple[Rule, ...]:
    rules = LINUX_RULES if format_name == "elf" else WINDOWS_RULES
    return tuple(rule for rule in rules if rule.data_mode in ("both", data_mode))
