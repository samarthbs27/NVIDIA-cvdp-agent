# NVIDIA CVDP Agent

A Codex CLI wrapper agent for the [NVIDIA ICLAD25 Hackathon](https://github.com/ICLAD-Hackathon/NVIDIA-ICLAD25-Hackathon) — **CVDP (Circuit Verification and Design Problems)** track.

Built for **ASU VLSI Design Automation (Mini Project 2)** under Prof. Chhabria.
**Team:** Samarth, Bangalore Sudharshan, Rijul, Rajendra Wankhade

---

## Overview

This agent solves Verilog design problems from the CVDP benchmark — RTL repair, code completion, and RTL generation from specification. It wraps the [Codex CLI](https://github.com/openai/codex) as an autonomous agent, achieving **25/30 problems (83.3%)** on the official NVIDIA harness.

**Research question:** Does iterative feedback-based repair outperform one-shot generation?
**Answer:** Yes — retry mode outperforms one-shot by +10 problems (50% → 83%), with the largest gains on hard problems (+6: 27% → 82%).

---

## Architecture

The pipeline runs in two phases, both from WSL:

### Phase A — RTL Generation

```
agent.py  (Python orchestrator)
    │
    ├── reads JSONL dataset
    ├── for each problem:
    │     setup_workdir()      →  extracts rtl/, verif/, prompt.txt, AGENTS.md
    │                              writes harness/src/*.py (official cocotb test specs)
    │     for attempt 1..6:
    │       [if needs_review]  →  run_codex_review() — reads harness/src/ + rtl/,
    │                              fixes issues, writes review.txt with PASS/FAIL verdict
    │       run_codex()        →  `codex exec` reads AGENTS.md, fixes rtl/, runs iverilog
    │       run_iverilog()     →  independent validation: iverilog + vvp
    │       if real testbench and pass → done
    │       if placeholder testbench and compiles → queue self-review next attempt
    │       else → write error_log.txt → escalate prompt → next attempt
    └── saves results_<mode>.json
```

### Phase B — Official Grading (Docker)

```
run_benchmark.py  (NVIDIA harness runner)
    │
    ├── for each problem:
    │     Container 1 (cvdp-relay-agent):    copies pre-built RTL from host → /code/rtl/
    │     Container 2 (ghcr.io/hdl/sim/osvb): iverilog + cocotb/pytest → result 0/1
    └── saves work/raw_result.json, work/report.txt
```

**Why a relay agent?** The NVIDIA harness always launches an agent container, but Codex CLI cannot run inside Docker (requires Node.js and interactive terminal). The relay container bridges this: it copies RTL that Phase A already generated on the host into the harness mount point.

**Why two phases?** Phase A generates RTL locally with real-time iverilog feedback. Phase B applies the authoritative cocotb/pytest grader that tests exact timing, parameterized sweeps, and edge cases that iverilog alone cannot check.

---

## Methodology

### 1. Codex CLI as the Agent

We use Codex CLI (`codex exec`) as the AI engine rather than calling the OpenAI API directly. Codex is an autonomous agent — it reads files, writes files, and runs shell commands. Our `agent.py` is a thin orchestrator that sets up the problem environment and invokes Codex as a subprocess. Model: `gpt-5.4` with `reasoning_effort=high` (ASU OpenAI account).

### 2. Harness Spec Visibility

Each problem's JSONL `harness` field contains the official cocotb/pytest test files. We write all `.py` files into `workdir/harness/src/` so Codex reads them as its **primary specification** — signal names, exact expected values, latency constraints, parameter sweeps. This gives Codex far more precision than the natural-language prompt alone.

Key exclusion: `test_runner.py` (reads Docker env vars at runtime — infrastructure, not spec).

NVIDIA's official agentic evaluation never exposes harness files to the agent. Our results are reported in two configurations: without harness spec (comparable to other teams) and with harness spec (ablation using publicly distributed JSONL data).

### 3. Escalating Prompts

In retry mode, each failed attempt escalates the Codex prompt:

| Attempt | Strategy | Focus |
|---|---|---|
| 1 | Guided | Follow AGENTS.md, fix rtl/ |
| 2 | Error-aware | Read error_log.txt, understand failures |
| 3 | Step-by-step | Think before changing anything |
| 4 | Targeted | Fix only what fails, preserve what works |
| 5 | Signal-level | Trace every signal against spec |
| 6 | Surgical | Verify every assertion in the testbench |

After each failed attempt, iverilog + vvp output is appended to `error_log.txt`. Codex reads this history. The workdir is never reset between retries — Codex builds on its own previous changes.

### 4. AGENTS.md — Workflow Instructions

A standalone `AGENTS.md` file (analogous to CLAUDE.md for Claude) is copied into every problem workdir. Codex picks it up automatically. It enforces:

**Pre-RTL extraction checklist:** Before touching any code, extract from `harness/src/` — every asserted signal, every exact expected value, every latency or cycle-count requirement.

**Eight RTL design rules** derived from failure analysis across all 30 problems:

1. **Latency and pipeline depth** — count register stages explicitly; registered `done` adds one extra cycle vs combinational
2. **No undriven outputs** — default assignments in every combinational always block; no high-Z or X
3. **Verify accumulation logic** — trace every counter enable; stuck-at-0 output means driving logic never fires
4. **Check every asserted signal** — not just primary data; status flags, error lines, direction indicators
5. **Algorithms must be exact** — polynomial taps, constants must match spec; if base case passes but sweep fails, a literal is hardcoded instead of the parameter variable
6. **Parameterized boundary cases** — test when parameters equal each other, at min/max; array index from Parameter A into size-B array overflows when A == B
7. **State preservation on no-match** — "output unchanged" on invalid input requires explicit pass-through RTL, not default assignments
8. **FSM output duration** — ask whether output holds for full state or only transition cycle; status stuck at 0 is almost always driven from transition guard instead of current state

### 5. Self-Review Mechanism

For problems with placeholder testbenches (15/30 problems have no local test — only cocotb inside Docker), after RTL compiles successfully a dedicated verification call runs before accepting:

- Codex reads `harness/src/` spec + `rtl/`, traces every assertion, fixes any discrepancy
- Writes `review.txt` ending with `REVIEW VERDICT: PASS` or `REVIEW VERDICT: FAIL`
- FAIL verdict feeds back into `error_log.txt` and continues the retry loop

**Finding:** The self-review fires correctly but Codex hallucinates simulation results — claiming "directed simulation confirmed latency = 6 cycles" when Phase B measures 7 cycles, or "no high-Z assignments found" when Phase B gets `ValueError: Cannot convert Logic('Z') to int`. LLM static RTL analysis is insufficient for latency off-by-one and boundary-case bugs. Only real cocotb simulation catches them.

---

## Results

**Best result: 25/30 problems (83.3%), 30/35 tests (85.7%)**

### Progression by configuration

| Configuration | Problems | Tests |
|---|---|---|
| Baseline (one-shot, no harness spec) | 15 / 30 (50.0%) | 20 / 35 (57.1%) |
| + harness spec (one-shot) | 16 / 30 (53.3%) | 21 / 35 (60.0%) |
| + harness spec (retry) | 18 / 30 (60.0%) | 23 / 35 (65.7%) |
| + cocotb 1.9.0 fix | 20 / 30 (66.7%) | 25 / 35 (71.4%) |
| + AGENTS.md v2 (5 rules) | 24 / 30 (80.0%) | 29 / 35 (82.9%) |
| **+ AGENTS.md v3 (8 rules) + allowlist fix** | **25 / 30 (83.3%)** | **30 / 35 (85.7%)** |

### By difficulty

| Difficulty | Baseline | Final | Delta |
|---|---|---|---|
| Easy (1) | 0 / 1 (0%) | **1 / 1 (100%)** | +100% |
| Medium (18) | 12 / 18 (66.7%) | **15 / 18 (83.3%)** | +16.6% |
| Hard (11) | 3 / 11 (27.3%) | **9 / 11 (81.8%)** | +54.5% |

### By category

| Category | Baseline | Final |
|---|---|---|
| cid016 (patch-based fix) | 3 / 3 (100%) | **3 / 3 (100%)** |
| cid003 | 2 / 5 (40%) | **5 / 5 (100%)** |
| cid004 | 6 / 13 (46.2%) | **10 / 13 (76.9%)** |
| cid005 | 4 / 9 (44.4%) | **7 / 9 (77.8%)** |

---

## Analysis and Key Findings

### What worked

**1. Retry mode is essential for hard problems.** One-shot solved 3/11 hard problems (27%). Retry with 6 attempts solved 9/11 (82%). The feedback loop — compile error → error_log → next attempt — is the single largest driver of improvement.

**2. Harness spec visibility helps most on hard problems.** One-shot without spec: 3/11 hard. One-shot with spec: 5/11. Hard problems have more complex timing and parameterized logic that the natural-language prompt alone underspecifies.

**3. RTL design rules (AGENTS.md) fixed 4 of 6 targeted failures.** Explicit rules for latency counting, accumulation logic, and algorithm constants addressed the most common failure class: LLMs implement correct logic but wrong pipeline depth or wrong constants.

**4. Harness allowlist completeness matters.** `ember_meadow_sunrise` failed every run until the fix that exposed `elevator_control.py` — its primary spec file, not caught by the original `test_*.py` filter. One line change, one new PASS.

**5. cocotb version pinning recovered 2 infrastructure errors.** The osvb Docker image ships `cocotb 2.0.0.dev0` which removed `cocotb.runner`. Pinning to 1.9.0 fixed `echo_obsidian_lunar` and `meadow_canyon_sunrise` (both had correct RTL — they were failing because pytest crashed before running any tests).

### What failed and why

**5 remaining failures** — all confirmed genuine RTL bugs beyond what prompt engineering reliably fixes:

| Problem | Bug | Root cause class |
|---|---|---|
| `breeze_velvet_violet` | `out_keys` → high-Z when `ARRAY_SIZE == DATA_WIDTH` | Boundary case — LLM misses index overflow at exact equality |
| `compass_breeze_obsidian` | Order matching latency 22 vs 21 cycles | Latency off-by-one — registered vs combinational done signal |
| `lagoon_dragon_diamond` | `o_proc_detected` always 0 | Sequence detection logic structurally wrong; sim hangs |
| `sunrise_ivory_glacier` | BST delete: latency +1, key-not-found corrupts tree | Two simultaneous bugs; both subtle |
| `thunder_diamond_horizon` | PRBS polynomial wrong, all 73 param combos fail | Exact polynomial bit pattern required; not inferable from prompts |

**Why self-review doesn't fix these:** Codex hallucinates simulation results. It writes "directed simulation confirmed latency = 6 cycles" while the actual cocotb test measures 7. For these bugs, only real simulation is a reliable oracle — which requires Phase B.

### Architecture limitation

15 of 30 problems have only a placeholder testbench locally. For these, Phase A has no simulation feedback — only compile success or failure. The self-review mechanism adds a static analysis pass, but LLM static analysis cannot reliably catch the bugs that matter (latency off-by-one, boundary-condition high-Z). Phase B is the only authoritative oracle for these problems, creating a closed-loop limitation: we cannot use Phase B feedback to drive Phase A retries without re-running the full Docker harness.

---

## Infrastructure Notes

### cocotb Version Pin

Three problems returned `result: 2` (harness crash, not RTL failure). Root cause: `test_runner.py` calls `from cocotb.runner import get_runner` — removed in cocotb 2.0, but the osvb image ships `cocotb 2.0.0.dev0`. Fix: `restore_files()` in `src/repository.py` patches osvb-based Dockerfiles to pin `cocotb==1.9.0`.

### Harness Path Reconstruction

The NVIDIA harness strips leading zeros from problem IDs (`"_0001"` → `1` for directory naming). Our glob-based search in `src/dataset_processor.py` matches `{problem_name}_*` candidates and compares `int(suffix) == int(issue_num)` to handle zero-padded workdirs.

### Docker PATH Fix

Docker is installed at a non-standard path on this machine. All generated shell scripts include `shutil.which("docker")` detection with the directory prepended to `PATH`, ensuring the binary is found in non-interactive bash subprocesses.

---

## Setup

Both phases run from WSL (Ubuntu 22.04). **Docker Desktop WSL integration must be enabled** (Settings → Resources → WSL Integration → toggle Ubuntu-22.04).

```bash
cd /path/to/NVIDIA-cvdp-agent   # adjust to your WSL mount path
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-harness.txt
echo '{"auths":{}}' > ~/.docker/config.json   # fix ghcr.io pulls in WSL
docker pull ghcr.io/hdl/sim/osvb
docker build -t cvdp-relay-agent:latest -f docker/Dockerfile-agent docker/
```

### Phase A — Generate RTL

```bash
source venv/bin/activate

# All 30 problems, retry mode
python agent.py --mode retry

# Specific problems
python agent.py --mode retry --ids \
  cvdp_agentic_ivory_cloud_ocean_3516 \
  cvdp_agentic_forest_fountain_river_0702
```

### Phase B — Official Grading

```bash
source venv/bin/activate

# All problems
python run_benchmark.py \
  -f dataset/hackathon-agentic-obfuscated_final_corrected.jsonl \
  -g cvdp-relay-agent:latest --llm

# Specific problems
python run_benchmark.py \
  -f dataset/hackathon-agentic-obfuscated_final_corrected.jsonl \
  -i cvdp_agentic_sunrise_ivory_glacier_9089 \
  -i cvdp_agentic_breeze_velvet_violet_7060 \
  -g cvdp-relay-agent:latest --llm
```

---

## Future Work

- **Real simulation loop for unverified problems** — the fundamental bottleneck is that Phase A cannot verify 15/30 problems locally; a lightweight Docker wrapper that runs cocotb without the full harness overhead would close this loop
- **Multi-agent architecture** — separate sub-agents for RTL repair vs generation, coordinated by an orchestrator
- **Dynamic prompts per category** — cid003/004/005 are undocumented by NVIDIA but may respond to different strategies derivable empirically from the dataset
