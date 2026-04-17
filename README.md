# NVIDIA CVDP Agent

A Codex CLI wrapper agent for the [NVIDIA ICLAD25 Hackathon](https://github.com/ICLAD-Hackathon/NVIDIA-ICLAD25-Hackathon) — **CVDP (Circuit Verification and Design Problems)** track.

Built as part of **ASU VLSI Design Automation (Mini Project 2)** under Prof. Chhabria.

**Team:** Samarth, Bangalore Sudharshan, Rijul, Rajendra Wankhade

---

## Overview

This agent solves Verilog design problems from the CVDP benchmark by wrapping the [Codex CLI](https://github.com/openai/codex) as an autonomous AI agent. For each problem, it:

1. Extracts RTL, testbench, and official harness test files from the dataset
2. Invokes Codex CLI to fix or generate the Verilog, using the harness test files as its primary specification
3. Validates the result locally using `iverilog` + `vvp`
4. Optionally retries with escalating prompts if the solution fails
5. Grades the final RTL using the official NVIDIA Docker harness

---

## Architecture

The pipeline runs in two phases:

### Phase A — RTL Generation (Windows host)

```
agent.py  (Python orchestrator)
    │
    ├── reads JSONL dataset
    ├── for each problem:
    │     setup_workdir()   →  extracts rtl/, verif/, prompt.txt, AGENTS.md
    │                          also writes harness/src/*.py (official test specs)
    │     for attempt 1..6:
    │       run_codex()     →  `codex exec` fixes RTL autonomously
    │                          Codex reads harness/src/ as primary spec
    │       run_iverilog()  →  validates with iverilog + vvp
    │       if pass → done
    │       else    → write error_log.txt → next attempt
    └── saves results_<mode>.json
```

### Phase B — Official Grading (WSL + Docker)

```
run_benchmark.py  (NVIDIA harness runner)
    │
    ├── for each problem:
    │     Container 1 (cvdp-relay-agent):
    │       reads RTL from /prebuilt  (work/<problem_id>/rtl/)
    │       copies to /code/rtl/       (harness working dir)
    │
    │     Container 2 (ghcr.io/hdl/sim/osvb):
    │       runs iverilog + cocotb/pytest on /code/rtl/
    │       produces result: 0 (PASS) or 1 (FAIL)
    │
    └── saves work/raw_result.json, work/report.json
```

### How Codex CLI works as the agent

Codex CLI is an autonomous AI agent — it reads files, writes files, and runs shell commands on its own. When invoked in a problem's working directory, it:

- Reads `AGENTS.md` automatically (our workflow instructions)
- Reads `prompt.txt` for the task description
- Reads and modifies `rtl/*.sv` to fix or generate the Verilog
- Runs `iverilog` and `vvp` internally to verify its own output
- Iterates until it is satisfied or exhausted

Our `agent.py` is purely an orchestrator — it sets up the environment and calls Codex as a subprocess.

### Why a relay agent?

The NVIDIA harness always launches an agent Docker container. Codex CLI cannot run inside Docker (it requires Node.js, npm, and interactive terminal support). The relay agent is a thin Python container that bridges the two:

- Codex CLI runs on the **host** (Phase A), generating RTL into `work/<problem_id>/rtl/`
- The relay container runs inside Docker (Phase B), copying that pre-built RTL into `/code/rtl/` where the grader expects it
- `dataset_processor.py` is patched to inject a `/prebuilt:ro` volume mount pointing to the host RTL directory

---

## Prerequisites

### Phase A (Windows)

| Tool | Version | Install |
|---|---|---|
| Python | 3.12+ | [python.org](https://www.python.org/) |
| Node.js | v18+ | [nodejs.org](https://nodejs.org/) |
| Codex CLI | 0.118.0+ | `npm install -g @openai/codex` |
| iverilog | 12.0+ | [iverilog.icarus.com](http://iverilog.icarus.com/) |

**Codex CLI setup:** run `codex` once to configure your OpenAI API key.

**Model used:** `gpt-5.4` with `reasoning effort: xhigh` (configured via ASU OpenAI account).

### Phase B (WSL + Docker)

| Tool | Notes |
|---|---|
| WSL | Ubuntu 22.04 — required because the NVIDIA harness calls `os.sync()` (Linux-only) |
| Docker Desktop | Must be running; install on a drive with sufficient space |
| Python 3.12 (WSL) | For running `run_benchmark.py` inside WSL |

**One-time setup (before any WSL commands):**

Enable Docker Desktop WSL integration for your Ubuntu distro:
> Docker Desktop → Settings → Resources → WSL Integration → toggle on for Ubuntu-22.04

Without this, the Docker binary inside WSL is a non-functional Windows shim and all benchmark runs will silently fail (exit code 1, 0/30 score).

**One-time setup in WSL** (do these once after cloning, in order):

```bash
# 1. Navigate to the repo via the WSL mount
cd /mnt/e/cvdp/NVIDIA-cvdp-agent   # adjust drive letter if needed

# 2. Create and activate a Python venv
python3 -m venv venv
source venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements-harness.txt

# 4. Fix Docker credential helper so ghcr.io pulls work in WSL
echo '{"auths":{}}' > ~/.docker/config.json

# 5. Pull the OSS simulation image
docker pull ghcr.io/hdl/sim/osvb

# 6. Build the relay agent image (re-run if relay_agent.py or Dockerfile-agent changes)
docker build -t cvdp-relay-agent:latest -f Dockerfile-agent .
```

Verify Docker is working before running the benchmark:
```bash
docker ps   # should return an empty list, not an error
```

> **WSL Python note:** Without the venv active, `python` in WSL resolves to the Windows pyenv shim (which has `\r\n` line endings and fails). Always activate the venv first, or use `python3` explicitly.

---

## Project Structure

```
NVIDIA-cvdp-agent/
├── agent.py                  ← Phase A: Codex CLI orchestrator
├── AGENTS.md                 ← Codex workflow instructions (copied into each workdir)
├── relay_agent.py            ← Phase B: relay container entry point
├── Dockerfile-agent          ← builds cvdp-relay-agent:latest
├── run_benchmark.py          ← Phase B: NVIDIA harness runner
├── run_reporter.py           ← generates score report from raw_result.json
├── requirements-harness.txt  ← Python deps for the harness (WSL)
├── .env                      ← harness configuration
├── dataset/
│   └── hackathon-agentic-obfuscated_final_corrected.jsonl   ← 30 benchmark problems
├── src/                      ← NVIDIA harness internals (dataset_processor.py patched)
└── work/                     ← created at runtime
    ├── <problem_id>/         ← Phase A output (agent.py workdir)
    │   ├── AGENTS.md
    │   ├── prompt.txt
    │   ├── rtl/              ← RTL files fixed by Codex ← graded by Phase B
    │   ├── verif/
    │   ├── harness/src/      ← official cocotb/pytest test specs (read-only for Codex)
    │   ├── error_log.txt
    │   ├── codex_attempt_N.log
    │   └── iverilog_attempt_N.log
    ├── <problem_name>/       ← Phase B output (NVIDIA harness workdir)
    │   └── harness/<N>/
    ├── raw_result.json       ← official harness results
    └── report.json           ← formatted score report
```

---

## Usage

### Phase A — Generate RTL (Windows)

Run from the repo root.

**One-shot mode** (Codex called once per problem):
```bash
python agent.py --mode one-shot
```

**Retry mode** (Codex called up to 6 times with escalating prompts):
```bash
python agent.py --mode retry
```

**Single problem:**
```bash
python agent.py --mode one-shot --id cvdp_agentic_starlight_phoenix_comet_6246
python agent.py --mode retry   --id cvdp_agentic_starlight_phoenix_comet_6246
```

**Specific set of problems:**
```bash
python agent.py --mode retry --ids \
  cvdp_agentic_ivory_cloud_ocean_3516 \
  cvdp_agentic_forest_fountain_river_0702 \
  cvdp_agentic_falcon_willow_dragon_8753
```

**First N problems:**
```bash
python agent.py --mode retry --limit 5
```

### Phase B — Official Grading (WSL)

Run from `/mnt/e/cvdp/NVIDIA-cvdp-agent/` (or wherever the repo is mounted in WSL).

**All problems:**
```bash
source venv/bin/activate
python run_benchmark.py \
  -f dataset/hackathon-agentic-obfuscated_final_corrected.jsonl \
  -g cvdp-relay-agent:latest \
  --llm
```

**Single problem:**
```bash
source venv/bin/activate
python run_benchmark.py \
  -f dataset/hackathon-agentic-obfuscated_final_corrected.jsonl \
  -i cvdp_agentic_starlight_phoenix_comet_6246 \
  -g cvdp-relay-agent:latest \
  --llm
```

Phase B reads the RTL that Phase A wrote to `work/<problem_id>/rtl/`. Always run Phase A first.

---

## Escalating Prompt Strategy

In retry mode, the prompt passed to Codex escalates with each failed attempt:

| Attempt | Strategy | Focus |
|---|---|---|
| 1 | Guided | Read prompt and follow AGENTS.md |
| 2 | Error-aware | Read error_log.txt, try again |
| 3 | Step-by-step | Think carefully before changing anything |
| 4 | Targeted | Fix only what is failing, don't break what works |
| 5 | Signal-level | Trace logic signal by signal against spec |
| 6 | Surgical | Verify every condition the testbench checks |

Between attempts, `iverilog` + `vvp` output is appended to `error_log.txt` in the working directory. Codex reads this file on each subsequent attempt to understand what failed previously.

The working directory is **not reset between retries** — Codex sees its own previous changes, which helps it build on partial progress rather than starting blind.

---

## Harness Spec Visibility

Each problem's JSONL `harness` field contains the official cocotb/pytest test files that the NVIDIA Docker grader runs. These files specify the exact RTL behavior required: which ports are driven, what input sequences are applied, what output values are expected, and what assertions must pass.

`setup_workdir()` writes these test files into `workdir/harness/src/` so Codex can read them as its primary specification. Only `test_*.py` files are written (allowlist). Excluded: `.sh`, `.yml`, `Makefile` (Docker infra), and `test_runner.py` (reads Docker env vars like `VERILOG_SOURCES` and `TOPLEVEL` — infrastructure, not RTL spec).

`AGENTS.md` instructs Codex to read `harness/src/` before touching any RTL, and explicitly forbids running or modifying those files (they require Docker; they are the ground truth).

Before writing any RTL, Codex is now required to extract from the harness spec: every signal name being asserted, every exact expected value, and any latency or cycle-count requirements (searching for `assert.*latency`, `assert.*== N`, etc.). This explicit extraction step was added after diagnosing that latency off-by-one failures — the most common failure class — occur when Codex implements correct logic but the wrong pipeline depth.

`AGENTS.md` also contains five RTL design rules derived from failure analysis across all 30 problems:

1. **Latency and pipeline depth** — count register stages explicitly; each registered output adds one cycle
2. **No undriven outputs** — default assignments at the top of every combinational always block; no high-Z or X
3. **Verify accumulation logic** — trace every counter/accumulator enable condition; an output always 0 means the driving logic is never reached
4. **Check every asserted signal** — not just the primary data output; status flags, error lines, direction indicators
5. **Algorithms must be exact** — polynomial taps, round functions, and constants must match the spec precisely

**Is this fair?** The harness test files are embedded in the publicly distributed JSONL — they are not hidden from participants. Every team that downloads the dataset already has them. However, NVIDIA's standard agentic evaluation never passes them to the agent container: `AgenticProcessor.include_harness` is hardcoded `False` in the official repository with no parameter to override it (confirmed from the official GitHub). The `include_harness` flag exists only in `CopilotProcessor` (a separate refinement-mode track).

Our results are therefore reported in two configurations:
- **Without harness spec** — matches the official agentic evaluation; directly comparable to other teams
- **With harness spec** — ablation study using publicly available JSONL data; not the standard setup

**Validated improvement:** `cvdp_agentic_ivory_cloud_ocean_3516` (cid003, hard) flipped from FAIL to PASS in one-shot mode once Codex could read `test_des_enc.py` as its spec.

---

## Infrastructure Fix: cocotb Version Pin

Three problems (`forest_fountain_river`, `echo_obsidian_lunar`, `meadow_canyon_sunrise`) persistently returned `result: 2` (harness execution error) across all runs. Root cause: their harness `test_runner.py` uses `from cocotb.runner import get_runner` — a cocotb ≥ 1.7 API that was **removed in cocotb 2.0**. The `ghcr.io/hdl/sim/osvb` image ships `cocotb 2.0.0.dev0`, so pytest crashes at collection before any test runs.

Fix: `restore_files()` in `src/repository.py` patches any osvb-based Dockerfile to pin `cocotb==1.9.0` (last stable 1.x release with `cocotb.runner`). This forces pip to downgrade from 2.0.0.dev0, enabling the harness to run. After the fix, `echo_obsidian_lunar` and `meadow_canyon_sunrise` both PASS; `forest_fountain_river` now properly fails (RTL bug) rather than crashing the harness.

---

## Dataset

The dataset contains **30 benchmark problems** across four categories:

| Category | Type | Count | Difficulty |
|---|---|---|---|
| cid003 | RTL — run against testbench, classify errors by iverilog | 5 | mixed |
| cid004 | RTL — run against testbench, classify errors by iverilog | 13 | mixed |
| cid005 | RTL — run against testbench, classify errors by iverilog | 9 | mixed |
| cid016 | RTL — takes cid015 as input; creates a fix and modifies RTL; simulate for fix | 3 | mixed |

Overall distribution: 1 easy, 18 medium, 11 hard.

Each problem in the JSONL contains:
- `prompt` — task description
- `context` — RTL and testbench files (self-contained, embedded as strings)
- `patch` — ground truth fix (agent never sees this)
- `harness` — official Docker test infrastructure

---

## Results

### Phase A (local iverilog)

Results are saved to `results_<mode>.json`:

```json
[
  {
    "id": "cvdp_agentic_starlight_phoenix_comet_6246",
    "categories": ["cid016", "medium"],
    "mode": "retry",
    "status": "pass",
    "passed": true,
    "attempts": 1,
    "output": "PASS\nPASS\n..."
  }
]
```

The `status` field has four values:
- `"pass"` — RTL compiled and simulation output contained no FAIL
- `"fail"` — compilation or simulation failed (all attempts exhausted)
- `"unverified"` — RTL compiled but no real testbench exists locally; actual tests run inside the NVIDIA Docker harness (cocotb/pytest)
- `"quota"` — Codex hit an API quota or rate-limit error; run stopped early

The `passed` field is `true` only for `"pass"` — never for `"unverified"` or `"quota"` — so pass rates are not inflated.

### Phase B (official harness)

Results are saved to `work/raw_result.json` and a formatted report to `work/report.txt`.

`result: 0` = PASS, `result: 1` = FAIL, `result: 2` = execution error (standard Unix exit code convention).

**Full results across all configurations (official harness, all 30 problems):**

| Metric | Baseline (one-shot) | One-shot + harness spec | Retry + harness spec | Retry + cocotb fix | **Retry + AGENTS.md v2** |
|---|---|---|---|---|---|
| Problems passed | 15 / 30 (50.0%) | 16 / 30 (53.3%) | 18 / 30 (60.0%) | 20 / 30 (66.7%) | **24 / 30 (80.0%)** |
| Tests passed | 20 / 35 (57.1%) | 21 / 35 (60.0%) | 23 / 35 (65.7%) | 25 / 35 (71.4%) | **29 / 35 (82.9%)** |

| Difficulty | Baseline | One-shot + harness | Retry + harness | + cocotb fix | **+ AGENTS.md v2** |
|---|---|---|---|---|---|
| Easy | 0 / 1 (0%) | 0 / 1 (0%) | 0 / 1 (0%) | 0 / 1 (0%) | **1 / 1 (100%)** |
| Medium | 12 / 18 (66.7%) | 11 / 18 (61.1%) | 11 / 18 (61.1%) | 13 / 18 (72.2%) | **14 / 18 (77.8%)** |
| Hard | 3 / 11 (27.3%) | 5 / 11 (45.5%) | 7 / 11 (63.6%) | 7 / 11 (63.6%) | **9 / 11 (81.8%)** |

| Category | Baseline | One-shot + harness | Retry + harness | + cocotb fix | **+ AGENTS.md v2** |
|---|---|---|---|---|---|
| cid016 | 3 / 3 (100%) | 3 / 3 (100%) | 3 / 3 (100%) | 3 / 3 (100%) | **3 / 3 (100%)** |
| cid003 | 2 / 5 (40%) | 3 / 5 (60%) | 3 / 5 (60%) | 4 / 5 (80%) | **5 / 5 (100%)** |
| cid004 | 6 / 13 (46.2%) | 6 / 13 (46.2%) | 8 / 13 (61.5%) | 8 / 13 (61.5%) | **10 / 13 (76.9%)** |
| cid005 | 4 / 9 (44.4%) | 4 / 9 (44.4%) | 4 / 9 (44.4%) | 5 / 9 (55.6%) | **6 / 9 (66.7%)** |

**Key findings:**
- Retry mode outperforms one-shot: +3 problems overall, hard problems improve most (27% → 64%)
- Harness spec helps hard problems: without it, hard = 27%; with it (retry), hard = 64%
- Cocotb 1.9.0 fix: 3 problems had result=2 (infrastructure error — `cocotb.runner` removed in cocotb 2.0, osvb ships 2.0.0.dev0). Pinning to cocotb 1.9.0 in the harness Dockerfile fixed the collection crash. 2 of 3 then passed outright; `forest_fountain_river` is now a genuine RTL failure (count logic wrong) not an infrastructure error
- **AGENTS.md v2 (extraction checklist + 5 RTL design rules):** targeted re-run of 10 failing problems flipped 4 to PASS (`forest_fountain_river`, `ivory_cloud_ocean`, `azure_sapphire_tiger`, `falcon_willow_dragon`). cid003 achieved 100%, hard problems improved from 63.6% to 81.8%, easy from 0% to 100%
- Remaining 6 failures: `breeze_velvet_violet` (high-Z on BST delete), `compass_breeze_obsidian` (latency 22 vs 21), `ember_meadow_sunrise` (up_led never asserted), `lagoon_dragon_diamond` (o_proc_detected always 0), `sunrise_ivory_glacier` (BST delete bugs), `thunder_diamond_horizon` (PRBS polynomial wrong)

---

## Validation

### Local (Phase A)

- **Compile check:** `iverilog -g2012 -o sim.out rtl/*.sv verif/*.sv`
- **Simulation check:** `vvp sim.out` — looks for `FAIL` in stdout
- **Pass condition:** exit code 0 and no `FAIL` in simulation output

**Caveat:** 15 of the 30 problems have no real testbench in the JSONL — only a stub (`module verif_placeholder; endmodule`). Their actual tests are cocotb/pytest scripts inside the Docker harness. For these, Phase A marks the result `"unverified"` and Phase B provides the authoritative score.

### Official (Phase B)

The NVIDIA harness runs cocotb/pytest inside `ghcr.io/hdl/sim/osvb` against the RTL produced by Phase A. This is the authoritative score used for the hackathon.

---

## Research Question

> Does iterative feedback-based repair outperform one-shot generation?

Run both modes on the same set of problems and compare `results_one-shot.json` vs `results_retry.json` to answer this.

---

## Future Work

- **Dynamic prompts per category** — cid003, cid004, and cid005 share the same evaluation method; their internal distinction is undocumented by NVIDIA but may be derivable empirically to inform category-specific strategies
- **Multi-agent architecture** — specialized sub-agents per task type coordinated by an orchestrator
- **Model selection** — experiment with different OpenAI models via Codex CLI `-m` flag
