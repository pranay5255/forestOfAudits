# Harbor Adapter

The Harbor adapter generates local Harbor task datasets from EVMBench audit
configs without replacing the existing nano path.

Generate a detect dataset:

```bash
uv run python -m evmbench.harbor_adapter generate-detect-dataset \
  --audit-id 2024-03-canto \
  --output-dir runs/harbor-datasets/detect-smoke \
  --overwrite
```

Run it with Harbor after installing the external Harbor CLI:

```bash
uv tool install harbor
harbor run -p runs/harbor-datasets/detect-smoke -a "<agent>" -m "<model>"
```

Generated tasks use the audit image from `Audit.docker_image`, so
`EVMBENCH_AUDIT_IMAGE_REPO` is honored when the dataset is generated. The detect
verifier reads `/home/agent/submission/audit.md`, calls the existing
`DetectGrader`, and writes `/logs/verifier/reward.json`.
The verifier expects the EVMBench Python package and its normal grading
dependencies to be importable inside Harbor's verifier process. For ad hoc local
experiments, `--include-source` stages the `evmbench/` package under
`/tests/evmbench-src`; use an audit image or verifier environment that already
has the Python dependencies installed.

Forest-of-Thought is exposed as an adapter around the existing Modal forest
runner. It remains an orchestrator wrapper rather than a Harbor multi-step task,
and publishes forest submissions, worker metadata, and trajectories through
Harbor artifacts.
