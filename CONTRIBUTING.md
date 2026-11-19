# Contributing

CrossGrid favors explainable review signals over broad, noisy rule counts.

- Every rule change should include positive and negative fixtures.
- Record whether a finding is a direct syntax observation or a heuristic inference.
- Do not claim that a runner succeeded merely because its label exists.
- When a matrix cannot be completely analyzed, preserve an unknown state rather than reporting a missing runner. Add a regression fixture for the boundary.
- Preserve source location, confidence and evidence through JSON, SARIF and HTML outputs.
- Keep output writes no-clobber by default; test both explicit overwrite and a target created concurrently. Never let `--overwrite` bypass scan-input or Git metadata guards.
- Do not execute scanned code or install repository dependencies.

Run `python -m unittest discover -s tests -v` with `PYTHONPATH=src` before proposing changes.
