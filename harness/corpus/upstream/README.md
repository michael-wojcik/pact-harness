# Recorded upstream frames

These files are parity cells harvested from PACT's own test suite, not
hand-written. They exist because synthetic frames cover every registration and
matcher token but not the input shapes upstream's tests actually exercise.

Each file is a corpus document in the format that pact conformance
--upstream-corpus FILE reads: recorded hook stdin frames with their cwd and home
placeholdered (<project>, <home>), deduplicated per hook, and with bulk payloads
above 64 KiB omitted. The recording tag is in the document header.

To regenerate or grow one:

    pact corpus harvest-upstream --repo ../PACT-Plugin --out harness/corpus/upstream/pact-<version>.json
    pact conformance --upstream-corpus harness/corpus/upstream/pact-<version>.json --parity

The qualify workflow's record_corpus input does the same on CI and uploads the
result as an artifact. Frames are reviewed and committed deliberately: they are
evidence, and a divergence they reveal is a harness bug before it is a corpus
problem.
