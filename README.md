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
| cid003 | RTL bug fixing | 5 | mixed |
| cid004 | Code completion | 13 | mixed |
| cid005 | RTL generation from spec | 9 | mixed |
| cid016 | Simulation/verification | 3 | mixed |

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
    "passed": true,
    "attempts": 1,
    "output": "PASS\nPASS\n..."
  }
]
```

The `attempts` field records how many Codex calls were made before pass or exhaustion. This enables direct comparison between one-shot and retry performance.

---

## Validation

We validate with local `iverilog` + `vvp` rather than the official NVIDIA Docker harness:

- **Compile check:** `iverilog -g2012 -o sim.out rtl/*.sv verif/*.sv`
- **Simulation check:** `vvp sim.out` — looks for `FAIL` in stdout
- **Pass condition:** exit code 0 and no `FAIL` in simulation output

This is sufficient for prototype evaluation. The testbenches embedded in the JSONL are the same ones the official harness uses.

---

## Research Question

> Does iterative feedback-based repair outperform one-shot generation?

Run both modes on the same set of problems and compare `results_one-shot.json` vs `results_retry.json` to answer this.

---

## Future Work

- **Dynamic prompts per category** — different strategies for repair (cid003), completion (cid004), generation (cid005), and verification (cid016)
- **Multi-agent architecture** — specialized sub-agents per task type coordinated by an orchestrator
- **Model selection** — experiment with different OpenAI models via Codex CLI `-m` flag
- **Official harness evaluation** — run passing solutions through the NVIDIA Docker harness for official scoring
