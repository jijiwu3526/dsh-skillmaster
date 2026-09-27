"""Read a preset's authoritative tool surface out of a DSH session archive.

The `request/header` event carries the exact tool list DSH sent to the model.
That is authoritative: it is the host's own view, not the model's self-report.
An earlier approach that asked the model to list its tools proved unreliable --
the model drifts in formatting and sometimes omits or invents entries.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


class ArchiveError(RuntimeError):
    pass


@dataclass
class Profile:
    """What one preset actually offers, measured from a real session."""

    preset: str
    tools: tuple[str, ...] = ()
    entries: int | None = None          # - id rows in agent.cordis.yml
    cwd: str | None = None
    session_id: str | None = None
    source_zip: str | None = None
    extras: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "preset": self.preset,
            "tools": list(self.tools),
            "tool_count": len(self.tools),
            "entries": self.entries,
            "cwd": self.cwd,
            "session_id": self.session_id,
            "source_zip": self.source_zip,
            **self.extras,
        }


def _read_log(archive: Path) -> list[dict]:
    try:
        with zipfile.ZipFile(archive) as z:
            names = [n for n in z.namelist() if n.endswith(".jsonl")]
            if not names:
                raise ArchiveError(f"{archive} 内没有 .jsonl 会话日志")
            raw = z.read(names[0]).decode("utf-8", "replace")
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"{archive} 不是有效的 ZIP") from exc
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def profile_from_archive(archive: Path) -> Profile:
    """Extract a preset profile from one exported session ZIP."""
    archive = Path(archive)
    events = _read_log(archive)
    if not events:
        raise ArchiveError(f"{archive} 的日志为空")

    header = events[0]
    if header.get("type") != "session":
        raise ArchiveError(f"{archive} 首条事件不是 session 头")
    preset = header.get("agentPreset") or ""

    tools: tuple[str, ...] = ()
    for event in events:
        if event.get("type") != "request/header":
            continue
        listed = ((event.get("data") or {}).get("header") or {}).get("tools")
        if isinstance(listed, list) and listed:
            names = []
            for item in listed:
                if isinstance(item, dict):
                    name = item.get("name") or (item.get("function") or {}).get("name")
                else:
                    name = item
                if isinstance(name, str) and name:
                    names.append(name)
            tools = tuple(sorted(set(names)))
            break

    return Profile(
        preset=preset,
        tools=tools,
        cwd=header.get("cwd"),
        session_id=header.get("id"),
        source_zip=str(archive),
    )


def diff_profiles(base: Profile, other: Profile) -> dict:
    """What `other` adds or drops relative to `base`."""
    b, o = set(base.tools), set(other.tools)
    return {
        "base": base.preset,
        "other": other.preset,
        "added": sorted(o - b),
        "removed": sorted(b - o),
        "shared": sorted(o & b),
    }


def count_entries(preset_yaml: Path) -> int | None:
    """Number of `- id:` plugin rows in a preset composition file."""
    try:
        text = Path(preset_yaml).read_text(encoding="utf-8")
    except OSError:
        return None
    total = 0
    for line in text.splitlines():
        if line.startswith("- id:"):
            total += 1
    return total or None
