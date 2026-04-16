# Agent: CVDP Verilog Engineer

## Goal
Read `prompt.txt` to understand the task. Fix or generate the Verilog/SystemVerilog
files in `rtl/` so that they compile cleanly and pass simulation.

## Steps
1. Read `prompt.txt` — this describes the exact task
2. If `harness/src/` exists, read every file in it — these are the official cocotb/pytest
   test scripts that define exactly what your RTL must do: which ports are driven, what
   input sequences are applied, what output values are expected, and what assertions must
   pass. Do NOT run these files (they require Docker) and do NOT modify them. Use them as
   your primary specification alongside `prompt.txt`.
3. Read all files in `rtl/` — these are the RTL files to fix or complete
4. Read files in `verif/` for context (testbench) — do NOT modify these
5. Fix or generate the RTL file(s) in `rtl/`
6. Compile: `iverilog -g2012 -Wall -o sim.out rtl/*.sv verif/*.sv`
   (adjust glob if files are .v not .sv)
7. If compilation fails, read the error, fix the RTL, and retry step 6
8. Simulate: `vvp sim.out`
9. If tests fail or show FAIL/ERROR, analyse the output, fix the RTL, retry from step 6
10. Continue until all tests pass or you have exhausted your best ideas
