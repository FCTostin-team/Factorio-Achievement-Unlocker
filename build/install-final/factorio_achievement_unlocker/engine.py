"""Validate a binary, plan every edit, then create a separate verified copy."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .profiles import Rule, rules_for


class PatchError(Exception):
    """An input cannot be safely patched or a managed output cannot be changed."""


@dataclass(frozen=True)
class Region:
    start: int
    end: int


@dataclass(frozen=True)
class Edit:
    name: str
    offset: int
    before: bytes
    after: bytes


@dataclass(frozen=True)
class Plan:
    format_name: str
    data_mode: str
    source_hash: str
    edits: tuple[Edit, ...]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _within(data: bytes, start: int, length: int) -> bool:
    return start >= 0 and length >= 0 and start + length <= len(data)


def executable_regions(data: bytes) -> tuple[str, tuple[Region, ...]]:
    """Return executable file ranges from an x86-64 PE or ELF image."""
    regions: list[Region] = []
    if data.startswith(b"\x7fELF"):
        if len(data) < 64 or data[4:7] != b"\x02\x01\x01":
            raise PatchError("Expected a little-endian, 64-bit ELF file")
        if struct.unpack_from("<H", data, 18)[0] != 62:
            raise PatchError("ELF machine is not x86-64")
        table = struct.unpack_from("<Q", data, 32)[0]
        entry_size, count = struct.unpack_from("<HH", data, 54)
        if entry_size < 56 or not _within(data, table, entry_size * count):
            raise PatchError("Invalid ELF program header table")
        for index in range(count):
            header = table + index * entry_size
            kind, flags = struct.unpack_from("<II", data, header)
            offset, size = struct.unpack_from("<QQ", data, header + 8)[0], struct.unpack_from("<Q", data, header + 32)[0]
            if kind == 1 and flags & 1 and size:
                if not _within(data, offset, size):
                    raise PatchError("Executable ELF segment extends beyond the file")
                regions.append(Region(offset, offset + size))
        format_name = "elf"
    elif data.startswith(b"MZ"):
        if len(data) < 64:
            raise PatchError("Truncated PE header")
        header = struct.unpack_from("<I", data, 0x3C)[0]
        if not _within(data, header, 24) or data[header:header + 4] != b"PE\0\0":
            raise PatchError("Invalid PE signature")
        machine, count = struct.unpack_from("<HH", data, header + 4)
        optional_size = struct.unpack_from("<H", data, header + 20)[0]
        if machine != 0x8664:
            raise PatchError("PE machine is not x86-64")
        optional = header + 24
        sections = optional + optional_size
        if optional_size < 2 or not _within(data, optional, optional_size) or struct.unpack_from("<H", data, optional)[0] != 0x20B:
            raise PatchError("Expected a PE32+ image")
        if not _within(data, sections, 40 * count):
            raise PatchError("Invalid PE section table")
        for index in range(count):
            section = sections + index * 40
            size, offset = struct.unpack_from("<II", data, section + 16)
            flags = struct.unpack_from("<I", data, section + 36)[0]
            if flags & 0x20000000 and size:
                if not _within(data, offset, size):
                    raise PatchError("Executable PE section extends beyond the file")
                regions.append(Region(offset, offset + size))
        format_name = "pe"
    else:
        raise PatchError("Expected a Factorio x86-64 PE or ELF executable")
    if not regions:
        raise PatchError("No executable code region found")
    regions.sort(key=lambda region: region.start)
    if any(left.end > right.start for left, right in zip(regions, regions[1:])):
        raise PatchError("Executable regions overlap")
    return format_name, tuple(regions)


def _signature(signature: str) -> tuple[bytes | None, ...]:
    try:
        result = tuple(None if token in ("?", "??") else bytes.fromhex(token) for token in signature.split())
    except ValueError as exc:
        raise PatchError("Invalid patch signature") from exc
    if not result or any(token is not None and len(token) != 1 for token in result):
        raise PatchError("Invalid patch signature")
    return result


def _matches(data: bytes, region: Region, rule: Rule) -> list[int]:
    signature = _signature(rule.signature)
    runs: list[tuple[int, bytes]] = []
    start = 0
    while start < len(signature):
        if signature[start] is None:
            start += 1
            continue
        end = start
        while end < len(signature) and signature[end] is not None:
            end += 1
        runs.append((start, b"".join(signature[start:end])))
        start = end
    if not runs:
        raise PatchError(f"Rule {rule.name} has no fixed bytes")
    anchor_index, anchor = max(runs, key=lambda pair: len(pair[1]))
    matches: list[int] = []
    cursor = region.start + anchor_index
    last_anchor = region.end - len(signature) + anchor_index
    while cursor <= last_anchor:
        found = data.find(anchor, cursor, last_anchor + len(anchor))
        if found < 0:
            break
        start = found - anchor_index
        if all(token is None or data[start + index:start + index + 1] == token for index, token in enumerate(signature)):
            matches.append(start)
        cursor = found + 1
    return matches


def build_plan(data: bytes, data_mode: str = "modded") -> Plan:
    if data_mode not in ("modded", "vanilla"):
        raise PatchError("Data mode must be modded or vanilla")
    format_name, regions = executable_regions(data)
    edits: list[Edit] = []
    for rule in rules_for(format_name, data_mode):
        found = [offset for region in regions for offset in _matches(data, region, rule)]
        if len(found) != rule.count:
            raise PatchError(f"{rule.name}: found {len(found)} match(es), expected {rule.count}; this game build is unsupported")
        for start in found:
            offset = start + rule.offset
            if offset < start or offset + len(rule.after) > start + len(_signature(rule.signature)):
                raise PatchError(f"{rule.name}: edit extends beyond its signature")
            if data[offset:offset + len(rule.before)] != rule.before:
                raise PatchError(f"{rule.name}: original opcode differs from the verified profile")
            edits.append(Edit(rule.name, offset, data[offset:offset + len(rule.after)], rule.after))
    edits.sort(key=lambda edit: edit.offset)
    if any(left.offset + len(left.after) > right.offset for left, right in zip(edits, edits[1:])):
        raise PatchError("Patch rules overlap")
    return Plan(format_name, data_mode, sha256(data), tuple(edits))


def apply_plan(data: bytes, plan: Plan) -> bytes:
    if sha256(data) != plan.source_hash:
        raise PatchError("Source changed since validation")
    patched = bytearray(data)
    for edit in plan.edits:
        if patched[edit.offset:edit.offset + len(edit.before)] != edit.before:
            raise PatchError(f"{edit.name}: bytes changed before patching")
        patched[edit.offset:edit.offset + len(edit.after)] = edit.after
    if len(patched) != len(data):
        raise PatchError("Patched file size changed unexpectedly")
    for edit in plan.edits:
        if patched[edit.offset:edit.offset + len(edit.after)] != edit.after:
            raise PatchError(f"{edit.name}: output verification failed")
    return bytes(patched)


def default_output(source: Path) -> Path:
    suffix = ".exe" if source.suffix.lower() == ".exe" else ""
    return source.with_name("factorio-unlocked" + suffix)


def state_path(output: Path) -> Path:
    return output.with_name(output.name + ".unlocker.json")


def _read_state(output: Path) -> dict | None:
    marker = state_path(output)
    if not marker.exists():
        return None
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PatchError(f"Cannot read managed output marker: {exc}") from exc
    if not isinstance(state, dict):
        raise PatchError("Managed output marker has an invalid format")
    if state.get("output") != str(output.resolve()):
        raise PatchError("Managed output marker points to a different path")
    return state


def _write_temp(directory: Path, data: bytes, mode: int | None = None) -> Path:
    fd, name = tempfile.mkstemp(prefix=".unlocker-", suffix=".tmp", dir=directory)
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            path.chmod(mode)
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def prepare(source: Path, output: Path | None = None, data_mode: str = "modded") -> tuple[Path, Plan, bool]:
    source = source.resolve(strict=True)
    if source.name.lower() not in ("factorio", "factorio.exe") or not source.is_file():
        raise PatchError("Source must be a Factorio executable named factorio or factorio.exe")
    output = default_output(source) if output is None else output.absolute()
    if output == source or output.resolve() == source:
        raise PatchError("Output must be separate from the original game executable")
    if not output.parent.is_dir():
        raise PatchError("Output directory does not exist")
    if output.is_symlink() or state_path(output).is_symlink():
        raise PatchError("Symlink outputs are not supported")
    data = source.read_bytes()
    plan = build_plan(data, data_mode)
    patched = apply_plan(data, plan)
    output_hash = sha256(patched)
    state = _read_state(output)
    if output.exists():
        if state is None:
            raise PatchError("Output already exists and is not managed by this tool")
        if sha256(output.read_bytes()) != state.get("output_sha256"):
            raise PatchError("Managed output was changed externally; refusing to overwrite it")
        if state.get("source") != str(source):
            raise PatchError("Managed output belongs to a different source")
        if state.get("source_sha256") == plan.source_hash and state.get("output_sha256") == output_hash and state.get("data_mode") == data_mode:
            return output, plan, False
    elif state is not None:
        raise PatchError("Managed output marker exists without its binary; remove the stale marker manually")
    if sha256(source.read_bytes()) != plan.source_hash:
        raise PatchError("Source changed while preparing the patch")
    manifest = {
        "schema": 1,
        "source": str(source),
        "source_sha256": plan.source_hash,
        "output": str(output.resolve()),
        "output_sha256": output_hash,
        "format": plan.format_name,
        "data_mode": data_mode,
        "edits": [{"name": edit.name, "offset": edit.offset, "before": edit.before.hex(), "after": edit.after.hex()} for edit in plan.edits],
    }
    binary_temp = marker_temp = None
    try:
        binary_temp = _write_temp(output.parent, patched, stat.S_IMODE(source.stat().st_mode))
        marker_temp = _write_temp(output.parent, (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
        if sha256(binary_temp.read_bytes()) != output_hash:
            raise PatchError("Temporary output failed verification")
        os.replace(binary_temp, output)
        binary_temp = None
        os.replace(marker_temp, state_path(output))
        marker_temp = None
    finally:
        if binary_temp is not None:
            binary_temp.unlink(missing_ok=True)
        if marker_temp is not None:
            marker_temp.unlink(missing_ok=True)
    return output, plan, True


def restore(output: Path) -> None:
    output = output.absolute()
    if output.is_symlink() or state_path(output).is_symlink():
        raise PatchError("Symlink outputs are not supported")
    state = _read_state(output)
    if state is None or not output.is_file():
        raise PatchError("No complete managed output found")
    if sha256(output.read_bytes()) != state.get("output_sha256"):
        raise PatchError("Managed output was changed externally; refusing to delete it")
    output.unlink()
    state_path(output).unlink()
