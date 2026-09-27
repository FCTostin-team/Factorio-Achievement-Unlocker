import json
import os
import stat
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import factorio_achievement_unlocker.engine as engine
from factorio_achievement_unlocker.engine import PatchError, Region, _matches, apply_plan, build_plan, prepare, restore, state_path
from factorio_achievement_unlocker.profiles import LINUX_RULES, WINDOWS_RULES, Rule


def code_bytes(rules):
    chunks = []
    for rule in rules:
        pattern = bytes(0 if token == "??" else int(token, 16) for token in rule.signature.split())
        for _ in range(rule.count):
            chunks.append(pattern + b"\xCC" * 32)
    return b"".join(chunks)


def elf_fixture():
    code = code_bytes(LINUX_RULES)
    image = bytearray(0x100 + len(code))
    image[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<H", image, 18, 62)
    struct.pack_into("<Q", image, 32, 64)
    struct.pack_into("<HH", image, 54, 56, 1)
    struct.pack_into("<II", image, 64, 1, 5)
    struct.pack_into("<Q", image, 72, 0x100)
    struct.pack_into("<Q", image, 96, len(code))
    image[0x100:] = code
    return bytes(image)


def pe_fixture():
    code = code_bytes(WINDOWS_RULES)
    image = bytearray(0x200 + len(code))
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", image, 0x84, 0x8664, 1)
    struct.pack_into("<H", image, 0x94, 0xF0)
    struct.pack_into("<H", image, 0x98, 0x20B)
    section = 0x80 + 24 + 0xF0
    image[section:section + 5] = b".text"
    struct.pack_into("<II", image, section + 16, len(code), 0x200)
    struct.pack_into("<I", image, section + 36, 0x60000020)
    image[0x200:] = code
    return bytes(image)


class EngineTests(unittest.TestCase):
    def test_match_at_end_of_executable_region(self):
        rule = Rule("boundary", "AA BB CC", 0, b"\xAA", b"\xDD")
        self.assertEqual(_matches(b"\x00\xAA\xBB\xCC", Region(1, 4), rule), [1])

    def test_both_formats_and_data_modes(self):
        for image, counts in ((elf_fixture(), (7, 8)), (pe_fixture(), (6, 8))):
            for mode, expected in zip(("modded", "vanilla"), counts):
                with self.subTest(format=image[:2], mode=mode):
                    plan = build_plan(image, mode)
                    self.assertEqual(len(plan.edits), expected)
                    result = apply_plan(image, plan)
                    self.assertEqual(len(result), len(image))
                    self.assertNotEqual(result, image)

    def test_no_partial_output_when_signature_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "factorio"
            source.write_bytes(elf_fixture().replace(b"\x74\x1A\x4C\x8B", b"\x74\x1B\x4C\x8B"))
            with self.assertRaisesRegex(PatchError, "unsupported"):
                prepare(source)
            self.assertFalse((source.parent / "factorio-unlocked").exists())

    def test_managed_copy_update_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "factorio.exe"
            original = pe_fixture()
            source.write_bytes(original)
            output, plan, created = prepare(source, data_mode="vanilla")
            self.assertTrue(created)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(output.read_bytes(), apply_plan(original, plan))
            self.assertEqual(json.loads(state_path(output).read_text())["data_mode"], "vanilla")
            self.assertFalse(prepare(source, data_mode="vanilla")[2])
            source.write_bytes(original + b"update")
            self.assertTrue(prepare(source, data_mode="vanilla")[2])
            restore(output)
            self.assertFalse(output.exists())
            self.assertFalse(state_path(output).exists())
            self.assertEqual(source.read_bytes(), original + b"update")

    def test_externally_changed_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "factorio"
            source.write_bytes(elf_fixture())
            output, _, _ = prepare(source)
            output.write_bytes(output.read_bytes() + b"external")
            with self.assertRaisesRegex(PatchError, "changed externally"):
                prepare(source)
            with self.assertRaisesRegex(PatchError, "changed externally"):
                restore(output)
            self.assertTrue(output.exists())

    def test_unmanaged_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "factorio"
            source.write_bytes(elf_fixture())
            output = source.parent / "factorio-unlocked"
            output.write_bytes(b"mine")
            with self.assertRaisesRegex(PatchError, "not managed"):
                prepare(source)
            self.assertEqual(output.read_bytes(), b"mine")

    def test_invalid_marker_fails_without_deleting_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "factorio"
            source.write_bytes(elf_fixture())
            output, _, _ = prepare(source)
            state_path(output).write_text("[]")
            with self.assertRaisesRegex(PatchError, "invalid format"):
                restore(output)
            self.assertTrue(output.exists())

    def test_failed_binary_replace_rolls_back_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "factorio"
            original = elf_fixture()
            source.write_bytes(original)
            output, _, _ = prepare(source)
            old_binary = output.read_bytes()
            old_marker = state_path(output).read_bytes()
            source.write_bytes(original + b"updated")
            replace = os.replace

            def fail_binary_replace(src, dst):
                if Path(dst) == output:
                    raise OSError("simulated replacement failure")
                return replace(src, dst)

            with mock.patch.object(engine.os, "replace", side_effect=fail_binary_replace):
                with self.assertRaisesRegex(OSError, "simulated replacement failure"):
                    prepare(source)
            self.assertEqual(output.read_bytes(), old_binary)
            self.assertEqual(state_path(output).read_bytes(), old_marker)

    def test_interrupted_update_can_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "factorio"
            original = elf_fixture()
            source.write_bytes(original)
            output, _, _ = prepare(source)
            previous_hash = engine.file_sha256(output)
            source.write_bytes(original + b"updated")
            plan = build_plan(source.read_bytes())
            state = json.loads(state_path(output).read_text())
            state["source_sha256"] = plan.source_hash
            state["output_sha256"] = engine.sha256(apply_plan(source.read_bytes(), plan))
            state["previous_output_sha256"] = previous_hash
            state_path(output).write_text(json.dumps(state))
            self.assertTrue(prepare(source)[2])
            self.assertEqual(engine.file_sha256(output), state["output_sha256"])

    def test_missing_copy_can_be_regenerated_from_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "factorio"
            source.write_bytes(elf_fixture())
            output, _, _ = prepare(source)
            output.unlink()
            self.assertTrue(prepare(source)[2])
            output.unlink()
            restore(output)
            self.assertFalse(state_path(output).exists())

    @unittest.skipIf(os.name == "nt", "Unix permission bits are unavailable on Windows")
    def test_special_permission_bits_are_not_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "factorio"
            source.write_bytes(elf_fixture())
            source.chmod(0o4755)
            output, _, _ = prepare(source)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o755)


if __name__ == "__main__":
    unittest.main()
