#!/usr/bin/env python3
"""DSH Conversation Studio CLI — preset memory and session orchestration.

Subcommands
-----------
  observe   Learn a preset's real tool surface from an exported session ZIP.
  choose    Record that you picked a preset (the habit signal).
  profile   Show what is remembered about one preset.
  compare   Diff two remembered presets.
  recommend Rank presets against tools you need / want to avoid.
  habit     Show which presets you actually use, overall or per project.
  incident  Record a failure you hit, so the next run can explain it.
  create    Create a session, seed it so the sidebar shows it, and remember
            the choice — the one-shot path this tool exists for.
  list      List remembered presets.

Memory lives in ~/.config/dsh-conversation-studio/memory.json (0600).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import dsh_session as dsh  # noqa: E402
from dshstudio.memory import Memory, default_path  # noqa: E402
from dshstudio.profile import (  # noqa: E402
    ArchiveError, Profile, count_entries, profile_from_archive,
)

PRESET_ROOT = Path.home() / ".dsh" / ".agent-presets"


def out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _preset_entries(preset: str) -> int | None:
    path = PRESET_ROOT / preset / "agent.cordis.yml"
    if not path.exists():
        return None
    return count_entries(path)


def _split(values: list[str] | None) -> list[str]:
    out_list: list[str] = []
    for value in values or []:
        out_list.extend(part.strip() for part in value.split(",") if part.strip())
    return out_list


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dshstudio",
        description="DSH 预设选型记忆与会话编排")
    ap.add_argument("--memory", type=Path, default=default_path(),
                    help="记忆文件路径（默认 ~/.config/dsh-conversation-studio/memory.json）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("observe", help="从会话导出 ZIP 学习预设的真实工具面")
    p.add_argument("archive", type=Path)
    p.add_argument("--notes", default="", help="这个预设适合什么场景")
    p.add_argument("--no-save", action="store_true", help="只打印不写记忆")

    p = sub.add_parser("choose", help="记录一次预设选择（形成使用习惯）")
    p.add_argument("preset")
    p.add_argument("--cwd")
    p.add_argument("--scenario", default="")
    p.add_argument("--session-id")

    p = sub.add_parser("profile", help="查看某个预设的记忆画像")
    p.add_argument("preset")

    p = sub.add_parser("compare", help="对比两个预设的记忆画像")
    p.add_argument("a")
    p.add_argument("b")

    p = sub.add_parser("recommend", help="按需要/不需要的工具推荐预设")
    p.add_argument("--want", action="append", help="需要的工具，可逗号分隔，可重复")
    p.add_argument("--avoid", action="append", help="不需要的工具，可逗号分隔，可重复")
    p.add_argument("--cwd", help="限定在某个项目下的使用习惯")

    p = sub.add_parser("habit", help="查看使用习惯")
    p.add_argument("--cwd")
    p.add_argument("--limit", type=int, default=5)

    p = sub.add_parser("incident", help="记录一次故障现象")
    p.add_argument("preset")
    p.add_argument("symptom")
    p.add_argument("--detail", default="")

    p = sub.add_parser("list", help="列出记忆中所有预设")
    p.add_argument("--detail", action="store_true")

    p = sub.add_parser("create", help="一站式：建会话 + 首发消息 + 记录选择")
    p.add_argument("--preset", required=True)
    p.add_argument("--text", required=True, help="让会话可见的首条消息")
    p.add_argument("--workspace-id", required=True)
    p.add_argument("--scenario", default="")
    p.add_argument("--url", default=None,
                   help="DSH Web 完整 URL。省略时由 dsh_session 自动解析"
                        "（桥接插件 → DSH_WEB_URL → 本机地址）")
    p.add_argument("--cookie-file", type=Path,
                    default=Path.home() / ".config/dsh-conversation-studio/cookies.txt")
    p.add_argument("--records-dir", type=Path, default=Path("records"))
    p.add_argument("--archive", type=Path, help="顺便导出到该 ZIP（用于 learn 工具面）")
    p.add_argument("--wait", type=int, default=60)
    p.add_argument("--timeout", type=float, default=30)

    args = ap.parse_args(argv)
    mem = Memory(args.memory)

    try:
        if args.cmd == "observe":
            profile = profile_from_archive(args.archive)
            entries = _preset_entries(profile.preset)
            if not args.no_save:
                mem.remember_profile(profile, notes=args.notes, entries=entries)
                mem.save()
            out({**profile.as_dict(), "entries": entries,
                 "notes": args.notes,
                 "saved": not args.no_save,
                 "memory_file": str(args.memory) if not args.no_save else None})

        elif args.cmd == "choose":
            mem.remember_choice(preset=args.preset, cwd=args.cwd,
                                scenario=args.scenario, session_id=args.session_id)
            mem.save()
            out({"recorded": True, "preset": args.preset,
                 "habit": mem.habit(args.cwd)})

        elif args.cmd == "profile":
            row = mem.profile(args.preset)
            if not row:
                print(f"错误：记忆里没有 {args.preset!r}。"
                      f"先用 observe 从会话导出 ZIP 学习它。", file=sys.stderr)
                return 1
            out(row)

        elif args.cmd == "compare":
            out(mem.compare(args.a, args.b))

        elif args.cmd == "recommend":
            out(mem.recommend(want=_split(args.want), avoid=_split(args.avoid),
                              cwd=args.cwd))

        elif args.cmd == "habit":
            out({"scope": args.cwd or "(全部项目)", "habit": mem.habit(args.cwd, args.limit)})

        elif args.cmd == "incident":
            mem.remember_incident(preset=args.preset, symptom=args.symptom,
                                  detail=args.detail)
            mem.save()
            out({"recorded": True, "incidents": mem.incidents(args.preset)})

        elif args.cmd == "list":
            profiles = mem.profiles()
            if args.detail:
                out(profiles)
            else:
                out({"count": len(profiles), "presets": [
                    {"preset": name, "tools": row.get("tool_count"),
                     "entries": row.get("entries"), "notes": row.get("notes") or ""}
                    for name, row in sorted(profiles.items())]})

        elif args.cmd == "create":
            # Only pass --url when the caller supplied one; otherwise let
            # dsh_session resolve it (bridge plugin → env → plain origin).
            # Passing the plain default here would bypass the bridge entirely.
            base = ["--cookie-file", str(args.cookie_file), "--timeout", str(args.timeout)]
            if args.url:
                base = ["--url", args.url, *base]

            rc = dsh.main([*base, "new", "--workspace-id", args.workspace_id,
                           "--preset", args.preset,
                           "--records-dir", str(args.records_dir)])
            if rc != 0:
                return rc
            receipts = sorted(args.records_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
            sid = json.loads(receipts[-1].read_text())["session_id"]
            sent = dsh.main([*base, "send", sid, "--text", args.text, "--wait", str(args.wait)])
            learned = None
            if args.archive:
                dsh.main([*base, "export", sid, "--output", str(args.archive)])
                if args.archive.exists():
                    profile = profile_from_archive(args.archive)
                    mem.remember_profile(profile, notes=args.scenario,
                                         entries=_preset_entries(profile.preset))
                    learned = profile.as_dict()
            mem.remember_choice(preset=args.preset, cwd=str(HERE), scenario=args.scenario,
                                session_id=sid)
            mem.save()
            out({"session_id": sid, "seeded": sent == 0,
                 "memory_file": str(args.memory),
                 "learned_profile": learned,
                 "habit": mem.habit()})
            return sent
    except (dsh.DshError, ArchiveError, KeyError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
