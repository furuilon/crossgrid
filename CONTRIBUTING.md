# Contributing

CrossGrid favors explainable review signals over broad, noisy rule counts.

- Every rule change should include positive and negative fixtures.
- Record whether a finding is a direct syntax observation or a heuristic inference.
- Do not claim that a runner succeeded merely because its label exists.
- Preserve source location, confidence and evidence through JSON, SARIF and HTML outputs.
- Do not execute scanned code or install repository dependencies.

Run `python -m unittest discover -s tests -v` with `PYTHONPATH=src` before proposing changes.
