# Hook latency, measured

The harness runs PACT's hooks as processes. That is correct — each hook reads
stdin once, calls sys.exit, and holds module-level state — but the cost is not
one number, and an early version of this document got that wrong. There are
three regimes, and they differ by more than an order of magnitude.

## The three regimes

Five hooks run on every `PreToolUse` `Edit` frame. Figures are on the machine
this harness was specified on, against PACT v4.8.36.

| Regime | Five PreToolUse Edit hooks | Notes |
|---|---|---|
| **First run, no bytecode cache** | **4,076–4,826 ms** | A fresh checkout. Python compiles the shared tree on every hook because nothing is cached yet. Measured on 3.12 and 3.14. |
| **Steady state, cache present** | **201–380 ms** | Bytecode present for every imported module. `bootstrap_gate` alone drops from ~1.2-1.5 s to ~60 ms. |
| **Warm transport** | **28–31 ms** | Worker start costs 111–167 ms once, including its own warm import. |

So the warm transport is a consistent **7–10x** improvement in steady state, and
it removes the first-run penalty entirely because the worker pays the compile
cost during startup instead of on the first tool call.

## What this decides

**Warm is the better default, not a rescue.** The earlier claim in this document
— that a cold transport would make PACT unusable — was based only on the
first-run regime and is not supported by the steady-state numbers. A 200–380 ms
pause before an edit is noticeable but ordinary; 30 ms is not noticeable at all.

The case for warm is still strong for three reasons:

1. It is 7–10x in the regime users actually sit in.
2. **A read-only vendored tree cannot write bytecode**, so an installed
   distribution that ships PACT read-only pays the first-run regime on every
   invocation. Warm converts that into a one-time startup cost.
3. It also removes variance: a cold first call after any cache invalidation is
   seconds, and warm is flat.

## Forking from a thread deadlocks (the most expensive finding)

The runtime originally ran a group's hooks concurrently in a thread pool. With
the warm transport that means **forking from worker threads**, and a child
forked that way inherits whatever locks the other threads held at that instant.
If one of those locks is needed by the hook's first import, the child never
returns.

The symptom was an evaluation child that hung roughly once every five calls,
with no error and no indication of which hook stalled. Three separate fixes were
needed to see it:

1. A watchdog so one hung evaluation cannot wedge the whole worker.
2. Moving the result across the process boundary through a temp file rather than
   a pipe, since a pipe's end-of-file waits for every process that inherited the
   write end -- including any descendant a hook left behind.
3. Making group concurrency conditional on the transport: `supports_parallel` is
   true for the subprocess transport and false for any transport that forks.

The third is the actual fix. Sequential forked hooks cost tens of milliseconds
and cannot deadlock. After it, 15 consecutive evaluations ran in 34 to 40 ms
each with no hang, and the Node suite passed eight consecutive stress runs.

## Two more fork hazards, both measured

- **The child must flush stdout and stderr before `os._exit`.** A forked hook
  that calls `os._exit` skips interpreter shutdown, so a buffered `print` is
  discarded and the decision silently vanishes. The first warm run produced
  empty output for every hook until the flush was added.
- **The child must rebind its Python streams to its descriptors.** Inheriting
  the parent's `sys.stdout` is wrong whenever the parent replaced it — a test
  harness, an embedder, a host that wraps stdout. An early version of the
  transport inherited pytest's capture object and wrote decisions into it while
  the exit code still looked correct.
- **The parent must flush before forking**, so its buffered output is not
  inherited and flushed into the child's captured pipe.

## Scoring the earlier estimates

The plan estimated roughly 0.7 s for a three-hook event, extrapolating from a
0.31 s single-hook measurement of `worktree_guard` — the fastest hook in the
set, in the steady-state regime.

The first correction (this document's previous version) then reported 4.8 s as
the representative cost, which was the *first-run* regime measured once, before
any bytecode cache existed.

Both were single-regime measurements presented as the cost. The table above is
the honest answer: the number depends on whether bytecode is cached, and a
transport choice should be justified on all three regimes rather than the most
dramatic one.
