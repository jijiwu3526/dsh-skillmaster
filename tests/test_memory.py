import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dshstudio.memory import Memory
from dshstudio.profile import (ArchiveError, count_entries, diff_profiles,
                               profile_from_archive)
from dshstudio.profile import Profile


def make_archive(tmp: Path, preset: str, tools: list[str], *, events=None) -> Path:
    """Build a session.v3.jsonl archive carrying an authoritative tool list."""
    path = tmp / f"{preset}.zip"
    rows = [{"type": "session", "version": 3, "id": f"session-{preset}",
             "cwd": f"/tmp/{preset}", "isSeeded": False,
             "agentPreset": preset}]
    rows.append({"type": "request/header",
                 "data": {"header": {"tools": [{"name": t} for t in tools]}}})
    for extra in (events or []):
        rows.append(extra)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("session.v3.jsonl",
                   "\n".join(json.dumps(r) for r in rows) + "\n")
    return path


class ProfileTests(unittest.TestCase):
    def test_extracts_authoritative_tools(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            a = make_archive(tmp, "alpha", ["bash", "read", "present"])
            p = profile_from_archive(a)
            self.assertEqual(p.preset, "alpha")
            self.assertEqual(p.tools, ("bash", "present", "read"))
            self.assertEqual(p.cwd, "/tmp/alpha")

    def test_missing_tool_event_yields_empty_not_error(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            a = make_archive(tmp, "bare", [])
            p = profile_from_archive(a)
            self.assertEqual(p.tools, ())

    def test_rejects_non_zip(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "x.zip"
            bad.write_bytes(b"not a zip")
            with self.assertRaises(ArchiveError):
                profile_from_archive(bad)

    def test_rejects_zip_without_log(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "e.zip"
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("readme.txt", "hi")
            with self.assertRaises(ArchiveError):
                profile_from_archive(p)

    def test_diff(self):
        a = Profile("a", ("bash", "read", "present"))
        b = Profile("b", ("bash", "read"))
        d = diff_profiles(a, b)
        self.assertEqual(d["removed"], ["present"])
        self.assertEqual(d["added"], [])
        self.assertEqual(d["shared"], ["bash", "read"])

    def test_count_entries(self):
        with tempfile.TemporaryDirectory() as d:
            y = Path(d) / "a.yml"
            y.write_text("- id: one\n- id: two\n# - id: three\n", encoding="utf-8")
            self.assertEqual(count_entries(y), 2)
            self.assertIsNone(count_entries(Path(d) / "nope.yml"))


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mem_path = Path(self._tmp.name) / "memory.json"
        self.mem = Memory(self.mem_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _profile(self, preset, tools):
        return Profile(preset=preset, tools=tuple(tools))

    def test_roundtrip(self):
        self.mem.remember_profile(self._profile("a", ["bash"]), notes="n")
        self.mem.save()
        again = Memory(self.mem_path)
        self.assertIn("a", again.profiles())
        self.assertEqual(again.profile("a")["tools"], ["bash"])

    def test_file_is_private(self):
        self.mem.remember_profile(self._profile("a", ["bash"]))
        self.mem.save()
        self.assertEqual(self.mem_path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_memory_does_not_raise(self):
        self.mem_path.write_text("{ this is not json", encoding="utf-8")
        m = Memory(self.mem_path)
        self.assertEqual(m.profiles(), {})

    def test_habit_counts_and_scopes(self):
        for _ in range(3):
            self.mem.remember_choice(preset="a", cwd="/proj/x")
        self.mem.remember_choice(preset="b", cwd="/proj/y")
        self.assertEqual(self.mem.habit("/proj/x")[0]["preset"], "a")
        self.assertEqual(len(self.mem.habit()), 2)

    def test_profile_change_is_dated(self):
        self.mem.remember_profile(self._profile("a", ["bash"]))
        first = self.mem.profile("a")["first_seen"]
        self.mem.remember_profile(self._profile("a", ["bash", "read"]))
        row = self.mem.profile("a")
        self.assertEqual(row["first_seen"], first)
        self.assertIn("tools_changed_at", row)

    def test_recommend_prefers_capability_over_habit(self):
        """A frequently used preset that lacks a wanted tool must still lose."""
        self.mem.remember_profile(self._profile("used", ["bash"]))
        self.mem.remember_profile(self._profile("fit", ["bash", "read"]))
        for _ in range(9):
            self.mem.remember_choice(preset="used", cwd="/p")
        r = self.mem.recommend(want=["read"], cwd="/p")
        self.assertEqual(r["candidates"][0]["preset"], "fit")

    def test_recommend_penalises_unwanted(self):
        self.mem.remember_profile(self._profile("lean", ["read"]))
        self.mem.remember_profile(self._profile("fat", ["read", "present"]))
        r = self.mem.recommend(want=["read"], avoid=["present"])
        self.assertEqual(r["candidates"][0]["preset"], "lean")

    def test_recommend_ignores_preset_without_memory(self):
        self.mem.remember_profile(self._profile("known", ["read"]))
        r = self.mem.recommend(want=["read"])
        self.assertEqual([c["preset"] for c in r["candidates"]], ["known"])

    def test_compare_missing_preset_raises(self):
        with self.assertRaises(KeyError):
            self.mem.compare("nope", "alsonope")

    def test_incidents_filter(self):
        self.mem.remember_incident(preset="a", symptom="boom")
        self.mem.remember_incident(preset="b", symptom="bang")
        self.assertEqual(len(self.mem.incidents("a")), 1)

    def test_preferences_are_bounded(self):
        for i in range(600):
            self.mem.remember_choice(preset="a", cwd=f"/p{i}")
        self.assertLessEqual(len(self.mem.preferences()), 500)


if __name__ == "__main__":
    unittest.main(verbosity=2)
