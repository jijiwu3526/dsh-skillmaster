"""Persistent, human-readable memory of preset capabilities and usage habits.

Design notes
------------
* One JSON document under the user config dir, so it is diffable, greppable
  and reviewable. The whole file is small (kilobytes), so there is no
  database and no migration story to own.
* Every fact is traceable. A profile records which archive it came from, and
  a preference records the session that motivated it, so a surprising
  recommendation can always be explained and corrected.
* Writes are atomic (temp file + replace) and the file is created 0600,
  because a memory file can leak project paths and preset intent.
* Nothing here is a cache of DSH state: DSH remains the source of truth for
  what exists. This file records what was *observed* and *chosen*.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from .profile import Profile, diff_profiles

SCHEMA_VERSION = 1
DEFAULT_DIR = Path.home() / ".config" / "dsh-conversation-studio"


def default_path() -> Path:
    return DEFAULT_DIR / "memory.json"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Memory:
    """Load/save/query the memory document."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_path()
        self.data: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "created_at": _now(),
            "updated_at": _now(),
            "profiles": {},
            "preferences": [],
            "incidents": [],
        }
        if self.path.exists() and self.path.stat().st_size:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                # An unreadable memory file must never block the tool. Start
                # over rather than crash; the old one is left on disk.
                loaded = None
            if isinstance(loaded, dict):
                self.data.update(loaded)
                self.data.setdefault("profiles", {})
                self.data.setdefault("preferences", [])
                self.data.setdefault("incidents", [])

    # ── persistence ──────────────────────────────────────────────────────
    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data["updated_at"] = _now()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        os.chmod(self.path, 0o600)
        return self.path

    # ── profiles ─────────────────────────────────────────────────────────
    def remember_profile(self, profile: Profile, *, notes: str = "",
                         entries: int | None = None) -> None:
        stored = profile.as_dict()
        stored["entries"] = entries if entries is not None else profile.entries
        stored["notes"] = notes
        stored["observed_at"] = _now()
        prev = self.data["profiles"].get(profile.preset)
        if prev:
            # Keep the first sighting so history stays readable.
            stored["first_seen"] = prev.get("first_seen", stored["observed_at"])
            if prev.get("tools") != profile.as_dict()["tools"]:
                stored["tools_changed_at"] = stored["observed_at"]
        else:
            stored["first_seen"] = stored["observed_at"]
        self.data["profiles"][profile.preset] = stored

    def profiles(self) -> dict[str, dict]:
        return dict(self.data.get("profiles") or {})

    def profile(self, preset: str) -> dict | None:
        return (self.data.get("profiles") or {}).get(preset)

    # ── preferences ──────────────────────────────────────────────────────
    def remember_choice(self, *, preset: str, cwd: str | None = None,
                        scenario: str = "", session_id: str | None = None) -> None:
        self.data["preferences"].append({
            "preset": preset,
            "cwd": cwd,
            "scenario": scenario,
            "session_id": session_id,
            "at": _now(),
        })
        # Keep the tail bounded; older choices stay visible via counts.
        if len(self.data["preferences"]) > 500:
            self.data["preferences"] = self.data["preferences"][-500:]

    def preferences(self) -> list[dict]:
        return list(self.data.get("preferences") or [])

    def habit(self, key: str | None = None, limit: int = 5) -> list[dict]:
        """Most-used presets overall, or scoped to a project/scenario."""
        counts: dict[str, int] = {}
        for row in self.preferences():
            if key and key not in (row.get("cwd") or ""):
                continue
            counts[row["preset"]] = counts.get(row["preset"], 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [{"preset": p, "uses": n} for p, n in ranked[:limit]]

    # ── incidents ────────────────────────────────────────────────────────
    def remember_incident(self, *, preset: str, symptom: str,
                          detail: str = "") -> None:
        self.data["incidents"].append({
            "preset": preset,
            "symptom": symptom,
            "detail": detail,
            "at": _now(),
        })
        if len(self.data["incidents"]) > 200:
            self.data["incidents"] = self.data["incidents"][-200:]

    def incidents(self, preset: str | None = None) -> list[dict]:
        rows = list(self.data.get("incidents") or [])
        if preset:
            rows = [r for r in rows if r.get("preset") == preset]
        return rows

    # ── analysis ─────────────────────────────────────────────────────────
    def capability_matrix(self) -> dict:
        """Which tools each known preset has, as a lookup table."""
        table: dict[str, set] = {}
        for name, row in (self.data.get("profiles") or {}).items():
            table[name] = set(row.get("tools") or [])
        return table

    def compare(self, a: str, b: str) -> dict:
        pa, pb = self.profile(a), self.profile(b)
        if not pa or not pb:
            missing = [n for n, v in ((a, pa), (b, pb)) if not v]
            raise KeyError(f"记忆里没有这些预设的画像：{missing}")
        fake_a = Profile(preset=a, tools=tuple(pa.get("tools") or []))
        fake_b = Profile(preset=b, tools=tuple(pb.get("tools") or []))
        return diff_profiles(fake_a, fake_b)

    def recommend(self, *, want: list[str] | None = None,
                  avoid: list[str] | None = None,
                  cwd: str | None = None) -> dict:
        """Rank known presets against a wanted/avoided tool list.

        This is a transparent, explainable ranking — deliberately not a model.
        The point is that the user can read exactly why something was
        suggested and correct it.
        """
        want_set = set(want or [])
        avoid_set = set(avoid or [])

        # Habit is a tie-breaker, not an override. A preset you use often but
        # that lacks a tool you explicitly asked for is still the wrong answer.
        habit = self.habit(cwd)
        uses = {row["preset"]: row["uses"] for row in habit}

        rows = []
        for name, row in (self.data.get("profiles") or {}).items():
            tools = set(row.get("tools") or [])
            missing = sorted(want_set - tools)
            unwanted = sorted(tools & avoid_set)
            fit = len(want_set) - len(missing) - 2 * len(unwanted)
            # Diminishing, capped bonus: familiarity can break a tie, never
            # outrank a real capability difference.
            bonus = min(1.0, uses.get(name, 0) / 5)
            rows.append({
                "preset": name,
                "score": round(fit + bonus, 2),
                "fit": fit,
                "habit_bonus": round(bonus, 2),
                "uses": uses.get(name, 0),
                "missing": missing,
                "unwanted": unwanted,
                "tool_count": len(tools),
                "explain": (
                    f"满足 {len(want_set) - len(missing)}/{len(want_set)} 项需求"
                    + (f"，多出 {len(unwanted)} 项不需要的工具" if unwanted else "")
                    + (f"，缺少 {missing}" if missing else "")
                    + (f"；你用过 {uses[name]} 次" if uses.get(name) else "")
                ) if want_set else f"共 {len(tools)} 个工具",
            })
        # Sort by capability first, then familiarity, then simplicity.
        rows.sort(key=lambda r: (-r["fit"], -r["uses"], r["tool_count"], r["preset"]))

        return {
            "criteria": {"want": sorted(want_set), "avoid": sorted(avoid_set),
                         "cwd": cwd},
            "candidates": rows,
            "habit": habit,
            "note": "先按功能匹配排序，用得多的只在打平时加分——"
                    "习惯不会盖过你明确提出的工具需求。"
                    "候选之外还有 DSH 内置预设，本工具没有画像就不会推荐。",
        }
