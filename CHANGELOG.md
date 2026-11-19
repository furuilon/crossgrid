# Changelog

## 0.2.0 — development snapshot (unreleased)

- Read bounded setuptools `setup.cfg` classifiers—including comma lists, the legacy alias, interpolation capped by 1 MiB output, 10 levels, 4,096 references and 4 Mi characters processed, and safe `file:` references—when no static project or Poetry classifiers take precedence.
- Report dynamic classifiers as unresolved without executing build metadata providers.
- Keep metadata references within in-root regular-file checks and output-alias protections, including unresolved in-root references.
- Derive the SARIF driver version from the package version.
