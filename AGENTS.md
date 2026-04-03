# Agent: CVDP Verilog Engineer

## Goal
Read `prompt.txt` to understand the task. Fix or generate the Verilog/SystemVerilog
files in `rtl/` so that they compile cleanly and pass simulation.

## Steps
1. Read `prompt.txt` — this describes the exact task
2. Read all files in `rtl/` — these are the RTL files to fix or complete
3. Read files in `verif/` for context (testbench) — do NOT modify these
4. Fix or generate the RTL file(s) in `rtl/`
5. Compile: `iverilog -g2012 -Wall -o sim.out rtl/*.sv verif/*.sv`
   (adjust glob if files are .v not .sv)
6. If compilation fails, read the error, fix the RTL, and retry step 5
7. Simulate: `vvp sim.out`
8. If tests fail or show FAIL/ERROR, analyse the output, fix the RTL, retry from step 5
9. Continue until all tests pass or you have exhausted your best ideas
