#!/usr/bin/env python3
"""
Single entry point for the NVIDIA CVDP Agent pipeline.

Usage:
    python run.py                          # Run on default dataset (30 problems)
    python run.py --dataset path/to.jsonl  # Run on a custom dataset
    python run.py --phase a                # Phase A only (RTL generation)
    python run.py --phase b                # Phase B only (official grading)
    python run.py --ids id1 id2            # Run specific problem IDs only
"""

import argparse
import subprocess
import sys
import json
import os
from pathlib import Path

ROOT = Path(__file__).parent.resolve()

DATASET     = "dataset/hackathon-agentic-obfuscated_final_corrected.jsonl"
AGENT_IMAGE = "cvdp-relay-agent:latest"
RESULTS_A   = "results/results_retry.json"
RESULTS_B   = "work/raw_result.json"


def run_phase_a(dataset: str, ids: list[str] | None) -> int:
    print("\n" + "=" * 60)
    print("PHASE A — RTL Generation (Codex CLI)")
    print("=" * 60)
    cmd = [sys.executable, "agent.py", "--mode", "retry", "--dataset", dataset]
    if ids:
        cmd += ["--ids"] + ids
    result = subprocess.run(cmd)
    return result.returncode


def run_phase_b(dataset: str, ids: list[str] | None) -> int:
    print("\n" + "=" * 60)
    print("PHASE B — Official Grading (Docker + cocotb)")
    print("=" * 60)
    cmd = [
        sys.executable, "run_benchmark.py",
        "-f", dataset,
        "-g", AGENT_IMAGE,
        "--llm",
    ]
    if ids:
        for id_ in ids:
            cmd += ["-i", id_]
    result = subprocess.run(cmd)
    return result.returncode


def print_summary():
    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)

    # Phase A summary (results_retry.json is a list of problem dicts)
    if Path(RESULTS_A).exists():
        with open(RESULTS_A) as f:
            data = json.load(f)
        rows   = data if isinstance(data, list) else list(data.values())
        passed = sum(1 for v in rows if v.get("passed"))
        total  = len(rows)
        print(f"Phase A  (local iverilog): {passed}/{total} problems passed")
    else:
        print("Phase A results not found.")

    # Phase B summary (raw_result.json is a dict; each value has "errors": 0 = PASS)
    if Path(RESULTS_B).exists():
        with open(RESULTS_B) as f:
            data = json.load(f)
        rows   = list(data.values()) if isinstance(data, dict) else data
        passed = sum(1 for v in rows if v.get("errors") == 0)
        total  = len(rows)
        print(f"Phase B  (official harness): {passed}/{total} problems passed")
    else:
        print("Phase B results not found.")

    print("=" * 60)
    print(f"Phase A results : {RESULTS_A}")
    print(f"Phase B results : {RESULTS_B}")
    print(f"Phase B report  : work/report.txt")


def main():
    parser = argparse.ArgumentParser(
        description="NVIDIA CVDP Agent — single entry point"
    )
    parser.add_argument(
        "--dataset", default=DATASET,
        help="Path to JSONL dataset (default: hackathon 30-problem set)"
    )
    parser.add_argument(
        "--phase", choices=["a", "b", "both"], default="both",
        help="Which phase to run: a (RTL generation), b (grading), both (default)"
    )
    parser.add_argument(
        "--ids", nargs="+", metavar="ID",
        help="Run only specific problem IDs (space-separated)"
    )
    args = parser.parse_args()

    if not Path(args.dataset).exists():
        print(f"ERROR: dataset not found: {args.dataset}")
        sys.exit(1)

    rc = 0
    if args.phase in ("a", "both"):
        rc = run_phase_a(args.dataset, args.ids)
        if rc != 0:
            print(f"\nWARNING: Phase A exited with code {rc}")
        os.chdir(ROOT)  # restore cwd in case Phase A subprocesses drifted it

    if args.phase in ("b", "both"):
        os.chdir(ROOT)  # ensure we start Phase B from the project root
        rc = run_phase_b(args.dataset, args.ids)
        if rc != 0:
            print(f"\nWARNING: Phase B exited with code {rc}")

    print_summary()


if __name__ == "__main__":
    main()
