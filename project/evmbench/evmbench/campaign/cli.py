"""Campaign control surface: plan / preflight / run / status / cancel / resume / summarize.

The CLI is the only supported entry point for experiments.  It resolves one
configuration object, validates it, and drives the detached coordinator.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from evmbench.agents.harness import SUPPORTED_HARNESSES
from evmbench.campaign.coordinator import (
    EXIT_CONFIG,
    EXIT_OK,
    CampaignCoordinator,
    make_sandbox_factory,
    plan_campaign,
    spawn_detached,
    store_for,
)
from evmbench.campaign.state import (
    DEFAULT_CANDIDATE_MODEL,
    DEFAULT_EVALUATOR_MODEL,
    BudgetSpec,
    CampaignConfig,
    CampaignStatus,
    CampaignStore,
    ConfigError,
    load_campaign_config,
)
from evmbench.utils import get_default_runs_dir


def _csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evmbench-campaign", description=__doc__)
    parser.add_argument("--config", type=Path, help="JSON config file (overrides flags).")
    parser.add_argument("--output-dir", type=Path, default=None, help="Campaign state root.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_config_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--backend", choices=["local", "modal"], default="modal")
        p.add_argument("--harness", choices=list(SUPPORTED_HARNESSES), default="opencode")
        p.add_argument("--strategy", choices=["baseline", "forest"], default="forest")
        p.add_argument("--mode", choices=["detect", "patch", "exploit"], default="detect")
        p.add_argument("--candidate-model", default=DEFAULT_CANDIDATE_MODEL)
        p.add_argument("--evaluator-model", default=DEFAULT_EVALUATOR_MODEL)
        p.add_argument("--audits", default="", help="Comma-separated audit ids.")
        p.add_argument("--hint-level", choices=["none", "low", "med", "high", "max"], default="none")
        p.add_argument("--branches-per-tree", type=int, default=1)
        p.add_argument("--tree-roles", default="")
        p.add_argument("--max-tree-roles", type=int, default=4)
        p.add_argument("--worker-concurrency", type=int, default=3)
        p.add_argument("--sandbox-cap", type=int, default=40)
        p.add_argument("--image-repo", default=os.getenv("EVMBENCH_AUDIT_IMAGE_REPO", "evmbench/audit"))
        p.add_argument(
            "--image-overrides",
            default="",
            help="Comma-separated audit=image overrides, e.g. 2024-03-gitcoin=repo:tag.",
        )
        p.add_argument("--continue-on-worker-error", action="store_true")
        p.add_argument("--campaign-id", default=None)

    for name in ("plan", "preflight", "run"):
        p = sub.add_parser(name)
        add_config_flags(p)
        if name == "preflight":
            p.add_argument("--refresh", action="store_true")

    for name in ("status", "cancel", "resume", "summarize"):
        p = sub.add_parser(name)
        p.add_argument("--campaign-id", required=True)

    return parser


def config_from_args(args: argparse.Namespace) -> CampaignConfig:
    if getattr(args, "config", None):
        config = load_campaign_config(args.config)
        if getattr(args, "output_dir", None):
            config = replace(config, output_dir=str(args.output_dir))
        return config
    overrides: dict[str, str] = {}
    for item in _csv(getattr(args, "image_overrides", "")):
        if "=" not in item:
            raise ConfigError(f"invalid --image-overrides entry {item!r}; expected audit=image")
        audit_id, image = item.split("=", 1)
        overrides[audit_id.strip()] = image.strip()
    budget = BudgetSpec()
    return CampaignConfig(
        backend=args.backend,
        harness=args.harness,
        strategy=args.strategy,
        mode=args.mode,
        candidate_model=args.candidate_model,
        evaluator_model=args.evaluator_model,
        audit_ids=_csv(args.audits),
        hint_level=args.hint_level,
        branches_per_tree=args.branches_per_tree,
        tree_roles=_csv(args.tree_roles),
        max_tree_roles=args.max_tree_roles,
        worker_concurrency=args.worker_concurrency,
        sandbox_cap=args.sandbox_cap,
        image_repo=args.image_repo,
        image_overrides=overrides,
        budget=budget,
        output_dir=str(
            args.output_dir
            or Path(get_default_runs_dir()) / "campaigns"
        ),
        continue_on_worker_error=args.continue_on_worker_error,
    )


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _store(args: argparse.Namespace, config: CampaignConfig | None = None) -> CampaignStore:
    if config is not None:
        return store_for(config)
    root = args.output_dir or Path(get_default_runs_dir()) / "campaigns"
    return CampaignStore(Path(root))


def _load_or_config(args: argparse.Namespace) -> CampaignConfig:
    """Reuse the stored config when the campaign already exists.

    ``preflight --campaign-id X`` and ``run --campaign-id X`` must not require
    re-specifying every flag; the campaign is the source of truth once created.
    """
    campaign_id = getattr(args, "campaign_id", None)
    if campaign_id:
        root = args.output_dir or Path(get_default_runs_dir()) / "campaigns"
        store = CampaignStore(Path(root))
        if store.campaign_path(campaign_id).exists():
            return store.config(campaign_id)
    return config_from_args(args)


def _cmd_plan(args: argparse.Namespace) -> int:
    config = _load_or_config(args)
    plan = plan_campaign(config, campaign_id=args.campaign_id, store=_store(args, config))
    _print(plan)
    return EXIT_OK


def _cmd_preflight(args: argparse.Namespace) -> int:
    config = _load_or_config(args)
    store = _store(args, config)
    plan = plan_campaign(config, campaign_id=args.campaign_id, store=store)
    campaign_id = plan["campaign_id"]
    coordinator = CampaignCoordinator(
        campaign_id=campaign_id,
        store=store,
        config=config,
        sandbox_factory=make_sandbox_factory(config),
    )
    try:
        reports = coordinator.preflight(refresh=args.refresh)
    except Exception as exc:  # noqa: BLE001 - preflight failure is a config error
        store.set_status(campaign_id, CampaignStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
        print(f"preflight failed: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    _print({"campaign_id": campaign_id, "preflight": reports})
    return EXIT_OK


def _cmd_run(args: argparse.Namespace) -> int:
    config = _load_or_config(args)
    store = _store(args, config)
    plan = plan_campaign(config, campaign_id=args.campaign_id, store=store)
    campaign_id = plan["campaign_id"]
    pid = spawn_detached(campaign_id, store)
    _print({"campaign_id": campaign_id, "coordinator_pid": pid, "plan": plan})
    return EXIT_OK


def _cmd_status(args: argparse.Namespace) -> int:
    store = _store(args)
    state = store.load_campaign(args.campaign_id)
    tasks = {}
    for task_id, task in state.get("tasks", {}).items():
        tasks[task_id] = {
            "status": task.get("status"),
            "grade": task.get("grade"),
            "stages": {
                attempt_id: attempt.get("stages", {})
                for attempt_id, attempt in task.get("attempts", {}).items()
            },
        }
    _print(
        {
            "campaign_id": args.campaign_id,
            "status": state.get("status"),
            "config": state.get("config"),
            "tasks": tasks,
            "summary": store.summary(args.campaign_id),
        }
    )
    return EXIT_OK


def _terminate_modal_sandboxes(campaign_id: str) -> int:
    try:
        import modal
    except ModuleNotFoundError:
        return 0
    terminated = 0
    try:
        app = modal.App.lookup("evmbench-campaign", create_if_missing=False)
        for sandbox in modal.Sandbox.list(app_id=app.app_id, tags={"campaign_id": campaign_id}):
            try:
                sandbox.terminate()
                terminated += 1
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001 - best effort
        return terminated
    return terminated


def _cmd_cancel(args: argparse.Namespace) -> int:
    store = _store(args)
    state = store.load_campaign(args.campaign_id)
    store.request_cancel(args.campaign_id, reason="cancel requested from CLI")
    pid_file = store.campaign_dir(args.campaign_id) / "coordinator.pid"
    signalled = False
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
            signalled = True
        except (ValueError, ProcessLookupError):
            signalled = False
    terminated = 0
    if state.get("backend") == "modal":
        terminated = _terminate_modal_sandboxes(args.campaign_id)
    _print({"campaign_id": args.campaign_id, "coordinator_signalled": signalled, "sandboxes_terminated": terminated})
    return EXIT_OK


def _cmd_resume(args: argparse.Namespace) -> int:
    store = _store(args)
    store.config(args.campaign_id)  # validate the stored configuration
    store.resume(args.campaign_id)
    store.clear_cancel(args.campaign_id)
    store.set_status(args.campaign_id, CampaignStatus.RUNNING)
    pid = spawn_detached(args.campaign_id, store)
    _print({"campaign_id": args.campaign_id, "coordinator_pid": pid})
    return EXIT_OK


def _cmd_summarize(args: argparse.Namespace) -> int:
    store = _store(args)
    _print(store.summary(args.campaign_id))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "plan": _cmd_plan,
        "preflight": _cmd_preflight,
        "run": _cmd_run,
        "status": _cmd_status,
        "cancel": _cmd_cancel,
        "resume": _cmd_resume,
        "summarize": _cmd_summarize,
    }
    try:
        return handlers[args.command](args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
