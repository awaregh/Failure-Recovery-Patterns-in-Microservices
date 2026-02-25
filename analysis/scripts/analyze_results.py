#!/usr/bin/env python3
"""
analyze_results.py – Parse k6 JSON output and generate summary tables.

Usage:
    python analysis/scripts/analyze_results.py results/payment_errors
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional


def parse_k6_summary(results_dir: Path) -> dict:
    """Parse k6 summary from all config subdirectories."""
    configs = {}
    for config_dir in sorted(results_dir.iterdir()):
        if not config_dir.is_dir():
            continue
        config = config_dir.name
        results_file = config_dir / "k6_results.json"
        output_file = config_dir / "k6_output.txt"

        metrics = {}

        # Parse structured JSON output
        if results_file.exists():
            metrics = _parse_k6_json(results_file)

        # Parse text output as fallback
        if not metrics and output_file.exists():
            metrics = _parse_k6_text(output_file)

        if metrics:
            configs[config] = metrics

    return configs


def _parse_k6_json(path: Path) -> dict:
    """Parse k6 --out json output."""
    metrics: dict = {
        "http_req_duration_p50": None,
        "http_req_duration_p95": None,
        "http_req_duration_p99": None,
        "http_req_failed_rate": None,
        "order_success_rate": None,
        "client_retries_total": 0,
        "iterations": 0,
    }
    try:
        durations = []
        failures = 0
        total = 0
        retries = 0

        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if obj.get("type") == "Point":
                    m = obj.get("metric", "")
                    v = obj.get("data", {}).get("value", 0)
                    if m == "http_req_duration":
                        durations.append(v)
                    elif m == "http_req_failed":
                        total += 1
                        if v == 1:
                            failures += 1
                    elif m == "client_retries_total":
                        retries += v

        if durations:
            durations_sorted = sorted(durations)
            n = len(durations_sorted)
            metrics["http_req_duration_p50"] = durations_sorted[int(n * 0.50)]
            metrics["http_req_duration_p95"] = durations_sorted[int(n * 0.95)]
            metrics["http_req_duration_p99"] = durations_sorted[int(n * 0.99)]
            metrics["iterations"] = n

        if total > 0:
            metrics["http_req_failed_rate"] = failures / total
            metrics["order_success_rate"] = 1 - (failures / total)

        metrics["client_retries_total"] = int(retries)

    except Exception as exc:
        print(f"  Warning: could not parse {path}: {exc}", file=sys.stderr)

    return metrics


def _parse_k6_text(path: Path) -> dict:
    """Parse k6 text output (fallback)."""
    metrics: dict = {}
    try:
        text = path.read_text()
        for line in text.splitlines():
            if "http_req_duration" in line and "avg=" in line:
                # Extract p50, p95, p99
                parts = line.split()
                for part in parts:
                    if part.startswith("p(50)="):
                        metrics["http_req_duration_p50"] = _parse_duration(part.split("=")[1])
                    elif part.startswith("p(95)="):
                        metrics["http_req_duration_p95"] = _parse_duration(part.split("=")[1])
                    elif part.startswith("p(99)="):
                        metrics["http_req_duration_p99"] = _parse_duration(part.split("=")[1])
            elif "http_req_failed" in line:
                for part in line.split():
                    if "%" in part:
                        try:
                            rate = float(part.replace("%", "")) / 100
                            metrics["http_req_failed_rate"] = rate
                            metrics["order_success_rate"] = 1 - rate
                        except ValueError:
                            pass
    except Exception:
        pass
    return metrics


def _parse_duration(s: str) -> Optional[float]:
    """Parse k6 duration string like '123.4ms' or '1.2s' to milliseconds."""
    s = s.strip()
    try:
        if s.endswith("ms"):
            return float(s[:-2])
        elif s.endswith("s"):
            return float(s[:-1]) * 1000
        elif s.endswith("µs"):
            return float(s[:-2]) / 1000
        return float(s)
    except ValueError:
        return None


def print_table(configs: dict) -> None:
    """Print a Markdown comparison table."""
    if not configs:
        print("No results found.")
        return

    headers = ["Config", "Success Rate", "P50 (ms)", "P95 (ms)", "P99 (ms)", "Error Rate", "Retries"]
    col_widths = [8, 13, 9, 9, 9, 11, 9]

    def row(cells):
        return "| " + " | ".join(str(c).ljust(w) for c, w in zip(cells, col_widths)) + " |"

    separator = "|-" + "-|-".join("-" * w for w in col_widths) + "-|"

    print()
    print("## Results Summary")
    print()
    print(row(headers))
    print(separator)

    for config, m in sorted(configs.items()):
        sr = f"{m.get('order_success_rate', 0) * 100:.1f}%" if m.get('order_success_rate') else "N/A"
        p50 = f"{m.get('http_req_duration_p50', 0):.0f}" if m.get('http_req_duration_p50') else "N/A"
        p95 = f"{m.get('http_req_duration_p95', 0):.0f}" if m.get('http_req_duration_p95') else "N/A"
        p99 = f"{m.get('http_req_duration_p99', 0):.0f}" if m.get('http_req_duration_p99') else "N/A"
        er = f"{m.get('http_req_failed_rate', 0) * 100:.1f}%" if m.get('http_req_failed_rate') is not None else "N/A"
        retries = str(m.get("client_retries_total", "N/A"))
        print(row([config, sr, p50, p95, p99, er, retries]))

    print()


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <results_dir>")
        sys.exit(1)

    results_dir = Path(sys.argv[1])
    if not results_dir.exists():
        print(f"Directory not found: {results_dir}")
        sys.exit(1)

    configs = parse_k6_summary(results_dir)
    print(f"Scenario: {results_dir.name}")
    print_table(configs)

    # Save markdown summary
    summary_path = results_dir / "summary.md"
    import io
    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()
    print(f"# Results: {results_dir.name}")
    print_table(configs)
    sys.stdout = old_stdout
    summary_path.write_text(buffer.getvalue())
    print(f"Summary saved to {summary_path}")


if __name__ == "__main__":
    main()
