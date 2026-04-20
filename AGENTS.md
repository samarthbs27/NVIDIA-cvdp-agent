# Agent: CVDP Verilog Engineer

## Goal
Read `prompt.txt` to understand the task. Fix or generate the Verilog/SystemVerilog
files in `rtl/` so that they compile cleanly and pass simulation.

## Steps
1. Read `prompt.txt` — this describes the exact task
2. If `harness/src/` exists, read every file in it before writing any RTL. These are the
   official cocotb/pytest test scripts and helper libraries. Before touching rtl/, extract from them:
   - Every signal name being asserted (inputs driven, outputs checked)
   - Every exact expected value (`assert dut.signal.value == N`)
   - Any latency or cycle-count requirements (search for "latency", "cycles", `assert.*== N`)
   - Any state or sequence the test walks through
   Do NOT run these files (they require Docker) and do NOT modify them. Treat every
   assertion as a hard constraint your RTL must satisfy exactly.
3. Read all files in `rtl/` — these are the RTL files to fix or complete
4. Read files in `verif/` for context (testbench) — do NOT modify these
5. Fix or generate the RTL file(s) in `rtl/`
6. Compile: `iverilog -g2012 -Wall -o sim.out rtl/*.sv verif/*.sv`
   (adjust glob if files are .v not .sv)
7. If compilation fails, read the error, fix the RTL, and retry step 6
8. Simulate: `vvp sim.out`
9. If tests fail or show FAIL/ERROR, analyse the output, fix the RTL, retry from step 6
10. Continue until all tests pass or you have exhausted your best ideas

## RTL Design Rules

1. **Latency and pipeline depth:** If the spec or test file states a cycle count or latency,
   count your pipeline register stages explicitly before writing the RTL. Each registered
   output adds exactly one cycle of latency. Design to hit the required count — do not
   assume "close enough" will pass.
   - Find the exact measurement point in the harness test: where does the cycle counter
     start, and what signal does it wait for to stop? A `done` signal assigned inside
     `always @(posedge clk)` adds one extra registered cycle compared to a combinational
     `assign done = ...`. If the test expects N cycles total and your logic completes in
     N−1 but you register `done`, you will measure N+1. Decide registered vs combinational
     based on what the test counts.

2. **No undriven outputs:** Every output must be driven to a defined binary value in every
   clock cycle, in every branch, in every FSM state. Never leave outputs implicitly undriven
   (high-Z or X). Place a default assignment at the top of every combinational always block
   so that every output has a fallback value regardless of which branch executes.

3. **Verify accumulation and counting logic:** If the spec requires a counter, accumulator,
   or edge-detection register, trace the increment/capture condition explicitly — confirm it
   fires on the correct clock edge under the correct enable conditions. An output that is
   always 0 means the driving logic is never reached; check every enable, reset, and
   clock-enable signal in the path.

4. **Check every output signal the test asserts:** Read the test spec and list every signal
   name that appears in an `assert` statement. Confirm your RTL drives each one with logic
   derived from the specification — not just the primary data output, but status flags,
   error signals, direction indicators, and interrupt lines too.

5. **Algorithms must be exact:** For PRBS, encryption, CRC, hashing, or any mathematical
   algorithm — implement the polynomial, tap positions, or round function from the spec
   precisely. Do not approximate or simplify. Verify every coefficient and constant against
   the spec before finalising the RTL.
   - If the test has a single hardcoded base case AND a parametrized sweep: a base-pass /
     sweep-fail means a formula uses a hardcoded literal instead of the parameter. Every
     tap index, polynomial coefficient, and shift count must reference the parameter
     variable, not the numeric value it happened to have for the first test case.

6. **Parameterized designs — boundary cases:** Before finalising RTL, mentally simulate
   the boundary conditions for every parameter: equal to each other, at minimum (0 or 1),
   at maximum. Array-driven output buses are the most common failure point — an index
   computed from Parameter A to address an array of size Parameter B can go out-of-bounds
   when A == B or when A > log2(B). For each packed or unpacked output array, verify the
   driving index stays in-bounds for every valid parameter combination. If any combination
   can produce an out-of-bounds index, add an explicit guard (`if (idx < SIZE) ...`) or
   restructure the index arithmetic to be safe by construction.

7. **State preservation on invalid or no-match input:** When the spec says the output
   should be "unchanged", "no effect", or "pass through" for an invalid key, missing entry,
   or out-of-range input — this is an active operation that requires explicit RTL. You must
   copy the input to the output (e.g., `assign out_array = in_array`) for the no-match
   case. Do NOT rely on default assignments that write sentinel values (zero, all-ones,
   high-Z) — those will silently overwrite the required unchanged output and the test will
   see corrupted values instead of the original.

8. **FSM designs — verify output duration:** For any FSM, before writing RTL build a
   simple output table: for each output port, write down the required value in every state.
   Then ask: should this output hold for the *entire duration* of a state, or only on the
   *cycle of the transition*? If the answer is "entire duration" (e.g. a direction indicator,
   a busy flag, a valid signal), drive it from the current state — not from transition guards
   or next-state comparisons, which are only true for one cycle. A status output stuck at 0
   in a test almost always means it was assigned inside a transition branch instead of
   against the current-state value. Every output port must appear in every FSM state branch;
   place default assignments at the top of the always block so no state accidentally leaves
   an output undriven.
