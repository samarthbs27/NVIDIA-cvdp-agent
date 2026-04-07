# NVIDIA CVDP Agent

A Codex CLI wrapper agent for the [NVIDIA ICLAD25 Hackathon](https://github.com/ICLAD-Hackathon/NVIDIA-ICLAD25-Hackathon) — **CVDP (Circuit Verification and Design Problems)** track.

Built as part of **ASU VLSI Design Automation (Mini Project 2)** under Prof. Chhabria.

**Team:** Samarth, Bangalore Sudharshan, Rijul, Rajendra Wankhade

---

## Overview

This agent solves Verilog design problems from the CVDP benchmark by wrapping the [Codex CLI](https://github.com/openai/codex) as an autonomous AI agent. For each problem, it:

1. Extracts RTL and testbench files from the dataset
2. Invokes Codex CLI to fix or generate the Verilog
3. Validates the result locally using `iverilog` + `vvp`
4. Optionally retries with escalating prompts if the solution fails

No Docker. No OpenAI API calls in our code. Runs natively on Windows.

---

## Architecture

```
agent.py  (Python orchestrator)
    │
    ├── reads JSONL dataset
    ├── for each problem:
    │     setup_workdir()   →  extracts rtl/, verif/, prompt.txt, AGENTS.md
    │     for attempt 1..6:
    │       run_codex()     →  `codex exec` fixes RTL autonomously
    │       run_iverilog()  →  validates with iverilog + vvp
    │       if pass → done
    │       else    → write error_log.txt → next attempt
    └── saves results_<mode>.json
```

### How Codex CLI works as the agent

Codex CLI is an autonomous AI agent — it reads files, writes files, and runs shell commands on its own. When invoked in a problem's working directory, it:

- Reads `AGENTS.md` automatically (our workflow instructions)
- Reads `prompt.txt` for the task description
- Reads and modifies `rtl/*.sv` to fix or generate the Verilog
- Runs `iverilog` and `vvp` internally to verify its own output
- Iterates until it is satisfied or exhausted

Our `agent.py` is purely an orchestrator — it sets up the environment and calls Codex as a subprocess.

---

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | 3.12+ | [python.org](https://www.python.org/) |
| Node.js | v18+ | [nodejs.org](https://nodejs.org/) |
| Codex CLI | 0.118.0+ | `npm install -g @openai/codex` |
| iverilog | 12.0+ | [iverilog.icarus.com](http://iverilog.icarus.com/) |

**Codex CLI setup:** run `codex` once in the terminal to configure your OpenAI API key. After that, no further key management is needed.

**Model used:** `gpt-5.4` with `reasoning effort: xhigh` (configured via ASU OpenAI account).

---

## Project Structure

```
CVDP_agent/
├── agent.py                  ← main orchestrator
├── AGENTS.md                 ← Codex workflow instructions (standalone, editable)
├── dataset/
│   └── hackathon-agentic-obfuscated_final_corrected.jsonl   ← 30 benchmark problems
├── work/                     ← per-problem working directories (created at runtime)
│   └── <problem_id>/
│       ├── AGENTS.md         ← copy of root AGENTS.md (Codex reads from its cwd)
│       ├── prompt.txt        ← task description (extracted from JSONL)
│       ├── rtl/              ← RTL files for Codex to fix (extracted from JSONL)
│       ├── verif/            ← testbench files, read-only (extracted from JSONL)
│       ├── error_log.txt     ← accumulated iverilog errors across retries
│       ├── codex_attempt_N.log     ← full Codex output per attempt
│       └── iverilog_attempt_N.log  ← full iverilog + vvp output per attempt
├── results_one-shot.json     ← results from one-shot mode (created at runtime)
├── results_retry.json        ← results from retry mode (created at runtime)
└── .gitignore
```

To modify Codex's workflow instructions, edit `AGENTS.md` directly — no need to touch `agent.py`.

---

## Usage

Run from inside the `CVDP_agent/` directory.

### One-shot mode (default)
Calls Codex once per problem, no retries:
```bash
python agent.py --mode one-shot
```

### Retry mode
Calls Codex up to 6 times per problem with escalating prompts:
```bash
python agent.py --mode retry
```

### Run a single problem
```bash
python agent.py --mode one-shot --id cvdp_agentic_starlight_phoenix_comet_6246
python agent.py --mode retry   --id cvdp_agentic_starlight_phoenix_comet_6246
```

### Run first N problems
```bash
python agent.py --mode one-shot --limit 5
```

### Combine flags
```bash
python agent.py --mode retry --limit 5
```

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
- `harness` — official Docker test infrastructure (not used in our approach)

---

## Results Format

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

The `status` field has three values:
- `"pass"` — RTL compiled and simulation output contained no FAIL
- `"fail"` — compilation or simulation failed (all attempts exhausted)
- `"unverified"` — RTL compiled but no real testbench exists locally; the actual tests run inside the NVIDIA Docker harness (cocotb/pytest) and cannot be run without Docker

The `passed` field is `true` only for `"pass"` — never for `"unverified"` — so pass rates are not inflated.

The `attempts` field records how many Codex calls were made before pass or exhaustion. This enables direct comparison between one-shot and retry performance.

---

## Validation

We validate with local `iverilog` + `vvp` rather than the official NVIDIA Docker harness:

- **Compile check:** `iverilog -g2012 -o sim.out rtl/*.sv verif/*.sv`
- **Simulation check:** `vvp sim.out` — looks for `FAIL` in stdout
- **Pass condition:** exit code 0 and no `FAIL` in simulation output

**Important caveat:** 15 of the 30 problems have no real testbench in the JSONL context — only a stub (`module verif_placeholder; endmodule`). Their actual tests are cocotb/pytest scripts that run inside the NVIDIA Docker harness. For these problems, we run Codex to fix/generate the RTL and confirm it compiles, but mark the result `"unverified"` rather than PASS. The real score for these can only be determined by running the official harness.

---

## Research Question

> Does iterative feedback-based repair outperform one-shot generation?

Run both modes on the same set of problems and compare `results_one-shot.json` vs `results_retry.json` to answer this.

---

## Future Work

- **Dynamic prompts per category** — cid003, cid004, and cid005 share the same evaluation method (iverilog + testbench); their internal distinction is not documented by NVIDIA but may be derivable empirically from the dataset to inform category-specific prompt strategies
- **Official harness evaluation** — run all 30 solutions through the NVIDIA Docker harness for authoritative scoring, including the 15 currently unverified problems
- **Multi-agent architecture** — specialized sub-agents per task type coordinated by an orchestrator
- **Model selection** — experiment with different OpenAI models via Codex CLI `-m` flag
- **Retry mode baseline** — compare retry vs one-shot pass rates across all 30 problems to validate the feedback loop hypothesis
