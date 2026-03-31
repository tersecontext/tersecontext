#!/usr/bin/env python3
"""Perf-tracker CLI — standalone client for the perf-tracker API."""
from __future__ import annotations
import argparse
import json
import os
import sys

import httpx


def _base_url(host: str, port: int) -> str:
    env = os.environ.get("PERF_TRACKER_URL")
    if env:
        return env.rstrip("/")
    return f"http://{host}:{port}"


def _print_table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("(no data)")
        return
    widths = {c: len(c) for c in columns}
    for row in rows:
        for c in columns:
            widths[c] = max(widths[c], len(str(row.get(c, ""))))
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    print(header)
    print("  ".join("-" * widths[c] for c in columns))
    for row in rows:
        line = "  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns)
        print(line)


def _print_hash(data: dict) -> None:
    if not data:
        print("(no data)")
        return
    max_key = max(len(str(k)) for k in data)
    for k, v in data.items():
        print(f"  {str(k).ljust(max_key)}  {v}")


def run_command(args: list[str], host: str, port: int) -> None:
    base = _base_url(host, port)
    use_json = "--json" in args
    args = [a for a in args if a != "--json"]

    cmd = args[0]
    repo = args[1] if len(args) > 1 else None

    if cmd == "status":
        resp = httpx.get(f"{base}/api/v1/realtime/{repo}")
        data = resp.json()
        if use_json:
            print(json.dumps(data, indent=2))
        else:
            print(f"Pipeline Status: {repo}")
            print()
            _print_hash(data)

    elif cmd == "bottlenecks":
        params = {}
        i = 2
        while i < len(args):
            if args[i] == "--category" and i + 1 < len(args):
                params["category"] = args[i + 1]
                i += 2
            elif args[i] == "--severity" and i + 1 < len(args):
                params["severity"] = args[i + 1]
                i += 2
            else:
                i += 1
        resp = httpx.get(f"{base}/api/v1/bottlenecks/{repo}", params=params)
        data = resp.json()
        if use_json:
            print(json.dumps(data, indent=2))
        else:
            print(f"Bottlenecks: {repo}")
            print()
            _print_table(data, ["entity_id", "category", "severity", "value", "unit", "detail"])

    elif cmd == "slow":
        params = {}
        i = 2
        while i < len(args):
            if args[i] == "--limit" and i + 1 < len(args):
                params["limit"] = args[i + 1]
                i += 2
            elif args[i] == "--commit" and i + 1 < len(args):
                params["commit_sha"] = args[i + 1]
                i += 2
            else:
                i += 1
        resp = httpx.get(f"{base}/api/v1/slow-functions/{repo}", params=params)
        data = resp.json()
        if use_json:
            print(json.dumps(data, indent=2))
        else:
            print(f"Slow Functions: {repo}")
            print()
            _print_table(data, ["entity_id", "value", "unit", "commit_sha"])

    elif cmd == "regressions":
        base_sha = head_sha = None
        i = 2
        while i < len(args):
            if args[i] == "--base" and i + 1 < len(args):
                base_sha = args[i + 1]
                i += 2
            elif args[i] == "--head" and i + 1 < len(args):
                head_sha = args[i + 1]
                i += 2
            else:
                i += 1
        if not base_sha or not head_sha:
            print("Error: --base and --head are required", file=sys.stderr)
            return
        resp = httpx.get(
            f"{base}/api/v1/regressions/{repo}",
            params={"base_sha": base_sha, "head_sha": head_sha},
        )
        data = resp.json()
        if use_json:
            print(json.dumps(data, indent=2))
        else:
            print(f"Regressions: {repo} ({base_sha[:7]} -> {head_sha[:7]})")
            print()
            _print_table(data, ["entity_id", "base_val", "head_val", "pct_change"])

    elif cmd == "trends":
        entity_id = args[2] if len(args) > 2 else ""
        window = "24h"
        i = 3
        while i < len(args):
            if args[i] == "--window" and i + 1 < len(args):
                window = args[i + 1]
                i += 2
            else:
                i += 1
        resp = httpx.get(
            f"{base}/api/v1/trends/{repo}/{entity_id}",
            params={"window": window},
        )
        data = resp.json()
        if use_json:
            print(json.dumps(data, indent=2))
        else:
            trend = data.get("trend", {})
            print(f"Trend: {entity_id} ({window})")
            print(f"  Direction: {trend.get('direction', 'unknown')}")
            print(f"  Slope:     {trend.get('slope', 0):.4f}")
            print(f"  Points:    {len(data.get('points', []))}")

    elif cmd == "summary":
        resp = httpx.get(f"{base}/api/v1/summary/{repo}")
        data = resp.json()
        if use_json:
            print(json.dumps(data, indent=2))
        else:
            print(f"Summary: {data.get('repo', repo)}")
            print()
            counts = data.get("metric_counts", {})
            print("Metric Counts:")
            for k, v in counts.items():
                print(f"  {k}: {v}")
            bottlenecks = data.get("active_bottlenecks", [])
            print(f"\nActive Bottlenecks: {len(bottlenecks)}")
            slow = data.get("top_slow_functions", [])
            if slow:
                print("\nTop Slow Functions:")
                _print_table(slow, ["entity_id", "value", "unit"])

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Perf-tracker CLI")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--json", action="store_true", dest="use_json")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    cmd_args = args.command
    if args.use_json:
        cmd_args.append("--json")
    run_command(cmd_args, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
