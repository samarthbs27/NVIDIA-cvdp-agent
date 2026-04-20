#!/usr/bin/env python3
"""
agent.py — Codex CLI wrapper for CVDP Verilog benchmark problems

For each problem in the JSONL dataset:
  1. Sets up a local working directory with the RTL + context files
  2. Writes AGENTS.md so Codex knows what to do
  3. Calls `codex exec` to fix/generate the RTL (up to MAX_ATTEMPTS times in retry mode)
  4. Validates the result with iverilog + vvp locally after each attempt
  5. Records pass/fail and attempt count to results.json

Usage:
  python agent.py                            # run all problems (one-shot mode)
  python agent.py --mode one-shot            # call codex once per problem
  python agent.py --mode retry               # call codex up to 6 times per problem
  python agent.py --id <problem_id>          # run a single problem
  python agent.py --ids id1 id2 id3          # run a specific set of problems
  python agent.py --limit 5                  # run first N problems
  python agent.py --mode retry --limit 5     # combine flags
"""

import json
import os
import stat
import subprocess
import sys
import shutil
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────
DATASET      = Path("dataset/hackathon-agentic-obfuscated_final_corrected.jsonl")
WORK_DIR     = Path("work")
MAX_ATTEMPTS = 6

# ── AGENTS.md — load from file (Codex reads this automatically from the workdir) ──
AGENTS_MD_FILE = Path("AGENTS.md")
if not AGENTS_MD_FILE.exists():
    print(f"ERROR: AGENTS.md not found at {AGENTS_MD_FILE.resolve()}")
    sys.exit(1)
AGENTS_MD_TEMPLATE = AGENTS_MD_FILE.read_text(encoding="utf-8")

# ── Escalating prompts ─────────────────────────────────────────────────────────
# Attempts 1-3: guided, work with what's there
# Attempts 4-6: analytical, targeted and rigorous
PROMPTS = {
    1: "Read prompt.txt and follow AGENTS.md to fix the RTL files in rtl/.",
    2: "The previous attempt failed. Read error_log.txt to understand the errors. Read prompt.txt again and fix the RTL files in rtl/.",
    3: "Two attempts have failed. Read error_log.txt carefully — all previous errors are recorded there. Re-read prompt.txt and fix the RTL files in rtl/. Think step by step before making changes.",
    4: "Three attempts have failed. Read error_log.txt and prompt.txt. Focus specifically on the part of the RTL causing the failure — do not change what is already working.",
    5: "Four attempts have failed. Read error_log.txt and prompt.txt. Trace through the logic signal by signal and verify each output matches what the specification requires.",
    6: "Five attempts have failed. Read error_log.txt and prompt.txt. Carefully verify every output and condition the testbench checks, and correct only what is wrong.",
}

# Prompt used for the self-review call on unverified problems (no local testbench).
# Codex reads the harness spec + RTL, finds discrepancies, fixes them, then writes
# a verdict to review.txt so we can decide whether to accept or retry.
REVIEW_PROMPT = (
    "The RTL in rtl/ has just been generated. Your task now is VERIFICATION then FIX.\n\n"
    "Step 1 — Extract spec requirements from harness/src/:\n"
    "  - Every output signal checked by assert and its required value\n"
    "  - Every parameter combination tested (especially boundary values where parameters equal each other, or hit min/max)\n"
    "  - Every latency or cycle count that is asserted (search for 'latency', 'cycles', 'assert.*==')\n"
    "  - Any key-not-found, invalid-input, or no-change semantics\n\n"
    "Step 2 — Trace each requirement through rtl/. Check for:\n"
    "  - Off-by-one latency (is done/complete registered or combinational?)\n"
    "  - Output buses that go high-Z at boundary parameter values (index overflow)\n"
    "  - Default assignments that overwrite no-change outputs on invalid input\n"
    "  - Algorithm constants that must use parameter variables, not hardcoded literals\n"
    "  - FSM status outputs set only on state transitions instead of held for the full state duration\n"
    "  - Accumulation or counter logic that never fires (stuck-at-0 outputs)\n\n"
    "Step 3 — Fix any issues you find directly in rtl/. Then recompile with iverilog to confirm no syntax errors.\n\n"
    "Step 4 — Write your analysis to review.txt:\n"
    "  - List each requirement and whether the RTL satisfies it\n"
    "  - Describe every change you made and why\n"
    "  - End with exactly one of:\n"
    "      REVIEW VERDICT: PASS\n"
    "      REVIEW VERDICT: FAIL"
)


# ── Setup ──────────────────────────────────────────────────────────────────────
def setup_workdir(problem: dict) -> Path:
    """Extract problem context files into a local working directory."""
    problem_id = problem["id"]
    workdir    = WORK_DIR / problem_id

    if workdir.exists():
        def remove_readonly(func, path, _):
            os.chmod(path, stat.S_IWRITE)
            func(path)
        shutil.rmtree(workdir, onexc=remove_readonly)
    workdir.mkdir(parents=True)

    # Write context files (rtl/, verif/, docs/, …)
    for rel_path, content in problem.get("context", {}).items():
        full_path = workdir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    # Write prompt as a plain text file for Codex to read
    (workdir / "prompt.txt").write_text(problem["prompt"], encoding="utf-8")

    # Write AGENTS.md — Codex picks this up automatically as workflow instructions
    # Note: we intentionally ignore the JSONL system_message — it was written for the
    # original ICLAD Linux/Docker harness (sed, awk, patch format) and is not compatible
    # with Codex CLI on Windows.
    (workdir / "AGENTS.md").write_text(AGENTS_MD_TEMPLATE, encoding="utf-8")

    # Write all .py files from the harness field so Codex can read the official
    # spec and any helper libraries it imports. Exclude only Docker infrastructure:
    # test_runner.py (reads Docker env vars — not an RTL spec), .sh, .yml, .env,
    # Makefile. Non-test Python files like elevator_control.py or harness_library.py
    # are valid spec/helper files that must be included.
    _HARNESS_EXCLUDE = {"test_runner.py"}
    for rel_path, content in problem.get("harness", {}).items():
        fname = os.path.basename(rel_path)
        if not fname.endswith(".py"):
            continue
        if fname in _HARNESS_EXCLUDE:
            continue
        full_path = workdir / "harness" / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    return workdir


# ── Error log ──────────────────────────────────────────────────────────────────
def write_error_log(workdir: Path, attempt: int, output: str) -> None:
    """Write iverilog output for a failed attempt to error_log.txt.
    Keeps only the last 2 entries to prevent runaway context growth across retries.
    """
    log_path = workdir / "error_log.txt"
    new_entry = f"=== Attempt {attempt} failed ===\n{output.strip()}\n\n"

    if log_path.exists():
        existing = log_path.read_text(encoding="utf-8")
        # Split on entry boundaries, drop oldest if we already have 2
        entries = [e for e in existing.split("=== Attempt ") if e.strip()]
        entries = ["=== Attempt " + e for e in entries]
        if len(entries) >= 2:
            entries = entries[-1:]  # keep only the most recent
        log_path.write_text("".join(entries) + new_entry, encoding="utf-8")
    else:
        log_path.write_text(new_entry, encoding="utf-8")


# ── Quota / rate-limit detection ──────────────────────────────────────────────
# Patterns that indicate Codex hit an API usage or rate limit rather than
# producing wrong RTL. Checked against codex_attempt_N.log after every failed run.
QUOTA_PATTERNS = [
    "exceeded your current quota",
    "insufficient_quota",
    "rate_limit_exceeded",
    "RateLimitError",
    "you have run out",
    "billing",
    "rate limit",
    "quota",
    "HTTP 429",
    "status 429",
]

def detect_quota_error(log_path: Path) -> str:
    """
    Scan codex_attempt_N.log for API quota or rate-limit error strings.
    Returns the matching line if found, empty string otherwise.
    """
    if not log_path.exists():
        return ""
    try:
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line_lower = line.lower()
            if any(p.lower() in line_lower for p in QUOTA_PATTERNS):
                return line.strip()
    except Exception:
        pass
    return ""


# ── Codex ──────────────────────────────────────────────────────────────────────
def run_codex(workdir: Path, prompt: str, attempt: int) -> bool:
    """
    Invoke Codex CLI in the working directory.
    --dangerously-bypass-approvals-and-sandbox : no confirmation prompts
    --skip-git-repo-check                      : workdirs are not git repos
    shell=True                                 : required on Windows for npm global binaries
    Saves stdout+stderr to codex_attempt_<N>.log in the workdir.
    Returns True if Codex exited cleanly.
    """
    log_path = workdir / f"codex_attempt_{attempt}.log"
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
            log_file.write(f"=== Codex attempt {attempt} ===\nPrompt: {prompt}\n\n")
            log_file.flush()
            result = subprocess.run(
                f'codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -c reasoning_effort=high "{prompt}"',
                cwd=workdir,
                timeout=1200,       # 20 minutes per codex call
                shell=True,         # required on Windows for npm global binaries
                stdin=subprocess.DEVNULL,  # prevent codex from waiting for Enter
                stdout=log_file,
                stderr=log_file,
            )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n[TIMED OUT after 20 min]\n")
        write_error_log(workdir, attempt,
            "WARNING: This attempt timed out and was killed after 20 minutes. "
            "RTL files in rtl/ may be truncated or incomplete due to the kill. "
            "Re-read prompt.txt and harness/src/ from scratch before making changes "
            "— do not try to patch what is currently in rtl/ without verifying it first.")
        print("  [codex timed out after 20 min]")
        return False


# ── Spec self-review ───────────────────────────────────────────────────────────
def run_codex_review(workdir: Path, attempt: int) -> tuple[bool, str]:
    """
    Run a verification-focused Codex call for unverified problems.
    Codex reads harness/src/ + rtl/, fixes issues it finds, then writes
    review.txt ending with 'REVIEW VERDICT: PASS' or 'REVIEW VERDICT: FAIL'.
    Returns (passed: bool, review_text: str).
    """
    log_path    = workdir / f"review_attempt_{attempt}.log"
    review_path = workdir / "review.txt"
    review_path.unlink(missing_ok=True)

    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
            log_file.write(f"=== Review attempt {attempt} ===\n\n")
            log_file.flush()
            subprocess.run(
                f'codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -c reasoning_effort=high "{REVIEW_PROMPT}"',
                cwd=workdir,
                timeout=1200,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=log_file,
            )
    except subprocess.TimeoutExpired:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n[TIMED OUT after 20 min]\n")
        return False, "Review call timed out — treating as unresolved."

    if not review_path.exists():
        return False, "Codex did not write review.txt — treating as unresolved."

    review_text = review_path.read_text(encoding="utf-8", errors="replace")
    passed = "REVIEW VERDICT: PASS" in review_text
    return passed, review_text


# ── Testbench check ────────────────────────────────────────────────────────────
PLACEHOLDER_CONTENTS = {
    "",
    "module verif_placeholder;\nendmodule",
    "module verif_placeholder;\r\nendmodule",
}

def has_real_testbench(workdir: Path) -> bool:
    """Return True if verif/ contains a real testbench (not just a placeholder)."""
    verif_dir = workdir / "verif"
    if not verif_dir.exists():
        return False
    tb_files = list(verif_dir.rglob("*.sv")) + list(verif_dir.rglob("*.v"))
    if not tb_files:
        return False
    for f in tb_files:
        content = f.read_text(encoding="utf-8", errors="replace").strip()
        if content not in PLACEHOLDER_CONTENTS:
            return True
    return False


# ── Validation ─────────────────────────────────────────────────────────────────
def run_iverilog(workdir: Path) -> tuple[bool, str, str]:
    """
    Compile and simulate the RTL with iverilog + vvp.
    Returns (passed: bool, compile_output: str, sim_output: str).
    """
    rtl_dir   = workdir / "rtl"
    verif_dir = workdir / "verif"

    rtl_files = (
        [str(p) for p in rtl_dir.rglob("*.sv")] +
        [str(p) for p in rtl_dir.rglob("*.v")]
    )
    tb_files = (
        [str(p) for p in verif_dir.rglob("*.sv")] +
        [str(p) for p in verif_dir.rglob("*.v")]
    ) if verif_dir.exists() else []

    if not rtl_files:
        return False, "No RTL files found in rtl/", ""

    sim_out = str(workdir.resolve() / "sim.out")

    # Compile
    compile_r = subprocess.run(
        ["iverilog", "-g2012", "-o", sim_out] + rtl_files + tb_files,
        capture_output=True, text=True
    )
    compile_out = compile_r.stdout + compile_r.stderr
    if compile_r.returncode != 0:
        return False, compile_out, ""

    # Simulate
    try:
        sim_r = subprocess.run(
            ["vvp", sim_out],
            capture_output=True, text=True, timeout=60,
            cwd=workdir,
        )
    except subprocess.TimeoutExpired:
        return False, compile_out, "Simulation timed out"

    sim_output = sim_r.stdout + sim_r.stderr
    passed = sim_r.returncode == 0 and "FAIL" not in sim_output.upper()

    # Delete VCD waveform files — large, not used by Codex or our validation
    for vcd in workdir.rglob("*.vcd"):
        vcd.unlink(missing_ok=True)

    return passed, compile_out, sim_output


def log_iverilog(workdir: Path, attempt: int, compile_out: str, sim_out: str, passed: bool) -> None:
    """Save full iverilog + vvp output for this attempt to iverilog_attempt_<N>.log."""
    log_path = workdir / f"iverilog_attempt_{attempt}.log"
    log_path.write_text(
        f"=== iverilog attempt {attempt} ===\n"
        f"Result: {'PASS' if passed else 'FAIL'}\n\n"
        f"--- compile ---\n{compile_out}\n"
        f"--- simulation ---\n{sim_out}\n",
        encoding="utf-8"
    )


# ── Problem runner ─────────────────────────────────────────────────────────────
def solve_problem(problem: dict, mode: str) -> dict:
    problem_id = problem["id"]
    categories = problem["categories"]

    print(f"\n{'='*65}")
    print(f"  ID         : {problem_id}")
    print(f"  Categories : {categories}")
    print(f"  Mode       : {mode}")

    # Setup workdir once — not reset between retries so Codex sees prior changes
    workdir = setup_workdir(problem)

    # Detect whether a real testbench exists in verif/
    # Problems with only a placeholder have cocotb-based tests in the Docker harness
    # that we cannot run locally — we still generate/fix the RTL but cannot verify it
    has_tb = has_real_testbench(workdir)
    if not has_tb:
        print(f"  Testbench  : NONE (placeholder only — result will be unverified)")

    max_iters   = 1 if mode == "one-shot" else MAX_ATTEMPTS
    passed      = False
    status      = "fail"
    output      = ""
    attempt     = 0
    needs_review = False   # set True after unverified compile-pass to trigger review next iteration
    harness_src  = workdir / "harness" / "src"

    for attempt in range(1, max_iters + 1):
        # ── Self-review branch (unverified problems only) ──────────────────────
        if needs_review and harness_src.exists():
            needs_review = False
            print(f"\n  [Attempt {attempt}/{max_iters}] Running spec self-review ...", flush=True)
            review_passed, review_text = run_codex_review(workdir, attempt)
            if review_passed:
                status = "unverified"
                print(f"  Result     : UNVERIFIED ⚠  (RTL compiles, self-review passed — attempt {attempt})")
                break
            else:
                print(f"  Result     : REVIEW FAIL ✗  (spec issues found — attempt {attempt})")
                write_error_log(workdir, attempt, review_text)
            continue

        prompt = PROMPTS[attempt]
        print(f"\n  [Attempt {attempt}/{max_iters}] Running codex ...", flush=True)

        run_codex(workdir, prompt, attempt)

        # Check for API quota / rate-limit before spending time on iverilog.
        # If Codex hit a quota wall, further retries won't help — bail out early.
        log_path = workdir / f"codex_attempt_{attempt}.log"
        quota_msg = detect_quota_error(log_path)
        if quota_msg:
            print(f"  [!] API quota/rate-limit detected: {quota_msg[:120]}")
            write_error_log(workdir, attempt,
                f"API quota or rate-limit hit — Codex could not complete this attempt.\n"
                f"Details: {quota_msg}")
            status = "quota"
            break

        print(f"  [Attempt {attempt}/{max_iters}] Validating with iverilog ...", flush=True)
        passed, compile_out, sim_out = run_iverilog(workdir)
        output = compile_out + sim_out

        if not has_tb:
            # No real testbench — check only that RTL compiles; simulation result is normally
            # meaningless. Exception: a timeout means the RTL itself has an infinite loop or
            # deadlock — that is a definitive failure worth feeding back to Codex.
            compiled  = (compile_out == "" or "error:" not in compile_out.lower())
            timed_out = (sim_out == "Simulation timed out")
            log_iverilog(workdir, attempt, compile_out, sim_out, compiled and not timed_out)
            if timed_out:
                print(f"  Result     : TIMEOUT ✗  (RTL compiles but simulation hangs — attempt {attempt})")
                write_error_log(workdir, attempt,
                    "Simulation timed out after 60 seconds. The RTL likely contains an infinite "
                    "loop, a missing $finish, or a deadlock. Inspect all always blocks and loops "
                    "for conditions that never terminate.")
            elif compiled:
                if harness_src.exists() and attempt < max_iters:
                    needs_review = True
                    print(f"  Result     : COMPILES ✓  (queuing spec self-review — attempt {attempt})")
                else:
                    status = "unverified"
                    print(f"  Result     : UNVERIFIED ⚠  (RTL compiles, no testbench — attempt {attempt})")
                    break
            else:
                print(f"  Result     : COMPILE FAIL ✗  (attempt {attempt})")
                snippet = compile_out.strip()[:200]
                print(f"  Output     : {snippet}")
                write_error_log(workdir, attempt, compile_out)
        else:
            log_iverilog(workdir, attempt, compile_out, sim_out, passed)
            if passed:
                status = "pass"
                print(f"  Result     : PASS ✓  (attempt {attempt})")
                break
            else:
                print(f"  Result     : FAIL ✗  (attempt {attempt})")
                snippet = output.strip()[:200]
                print(f"  Output     : {snippet}")
                write_error_log(workdir, attempt, output)

    if status == "fail":
        print(f"\n  Final      : FAIL ✗  (all {max_iters} attempt(s) exhausted)")

    return {
        "id":         problem_id,
        "categories": categories,
        "mode":       mode,
        "status":     status,
        "passed":     status == "pass",
        "attempts":   attempt,
        "output":     output.strip()[:500],
    }


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    if not DATASET.exists():
        print(f"Dataset not found: {DATASET}")
        sys.exit(1)

    with open(DATASET, encoding="utf-8") as f:
        problems = [json.loads(line) for line in f if line.strip()]

    # --mode one-shot | retry  (default: one-shot)
    mode = "one-shot"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
        if mode not in ("one-shot", "retry"):
            print(f"Invalid mode '{mode}'. Use: one-shot | retry")
            sys.exit(1)

    # --id <problem_id>  →  run a single problem
    if "--id" in sys.argv:
        target   = sys.argv[sys.argv.index("--id") + 1]
        problems = [p for p in problems if p["id"] == target]
        if not problems:
            print(f"Problem '{target}' not found in dataset")
            sys.exit(1)

    # --ids id1 id2 ...  →  run a specific set of problems (all remaining argv after --ids)
    if "--ids" in sys.argv:
        idx    = sys.argv.index("--ids") + 1
        # collect all args after --ids that don't start with --
        targets = []
        while idx < len(sys.argv) and not sys.argv[idx].startswith("--"):
            targets.append(sys.argv[idx])
            idx += 1
        if not targets:
            print("--ids requires at least one problem ID")
            sys.exit(1)
        problems = [p for p in problems if p["id"] in targets]
        missing  = set(targets) - {p["id"] for p in problems}
        if missing:
            print(f"Problems not found in dataset: {', '.join(sorted(missing))}")
            sys.exit(1)

    # --limit N  →  run only the first N problems
    if "--limit" in sys.argv:
        n        = int(sys.argv[sys.argv.index("--limit") + 1])
        problems = problems[:n]

    WORK_DIR.mkdir(exist_ok=True)
    results = []

    quota_hit = False
    for problem in problems:
        result = solve_problem(problem, mode)
        results.append(result)
        if result.get("status") == "quota":
            print("\n  [!] API quota exhausted — stopping run. Re-run when quota resets.")
            quota_hit = True
            break

    # ── Summary ────────────────────────────────────────────────────────────────
    total      = len(results)
    n_pass     = sum(1 for r in results if r.get("status") == "pass")
    n_unver    = sum(1 for r in results if r.get("status") == "unverified")
    n_fail     = sum(1 for r in results if r.get("status") == "fail")
    n_quota    = sum(1 for r in results if r.get("status") == "quota")
    pct        = (100 * n_pass // total) if total else 0
    verifiable = total - n_unver
    pct_ver    = (100 * n_pass // verifiable) if verifiable else 0

    print(f"\n{'='*65}")
    print(f"  MODE       : {mode}")
    print(f"  TOTAL      : {total}")
    print(f"  PASSED     : {n_pass}  ({pct}% of all, {pct_ver}% of verifiable)")
    print(f"  FAILED     : {n_fail}")
    print(f"  UNVERIFIED : {n_unver}  (RTL compiles; no local testbench)")
    if n_quota:
        print(f"  QUOTA HIT  : {n_quota}  (API quota exhausted — run stopped early)")

    results_file = f"results_{mode}.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Saved → {results_file}")


if __name__ == "__main__":
    main()
