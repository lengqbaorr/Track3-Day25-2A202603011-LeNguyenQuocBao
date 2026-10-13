"""Reproduce report ablations without modifying the production config or client."""
from __future__ import annotations

import copy
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gameday_lab.client import ResilientClient
from gameday_lab.factory import build_client
from gameday_lab.sim.config import load_config
from gameday_lab.sim.engine import evaluate_checks, run_scenario


class UnfilteredClient(ResilientClient):
    """Deliberately unsafe ablation, only used by this experiment."""

    def ladder(self, task: str) -> list[str]:
        return list(self.order)


def main() -> None:
    base = load_config()
    cache_base = load_config(scenarios_path="configs/scenarios_cache.yaml")
    results = {}

    def run(label, cfg, keys, client_name="resilient", unfiltered=False):
        rows = {}
        for key in keys:
            client = (UnfilteredClient(cfg.provider_infos(), cfg.fallback_order, cfg.client, seed=1)
                      if unfiltered else build_client(client_name, cfg))
            metrics = run_scenario(cfg, key, client, seed=25).metrics()
            metrics["checks"] = evaluate_checks(metrics, cfg.scenarios[key].checks)
            rows[key] = metrics
        results[label] = rows
        print(label, {key: {k: m.get(k) for k in ("availability", "latency_p95_s", "amplification",
              "retry_ratio", "recovery_s", "incompatible_routes", "cache_hit_ratio", "wrong_answers",
              "outdated_answers", "faq_availability", "cost_per_good_turn")} for key, m in rows.items()}, flush=True)

    for disabled in (False, True):
        settings = copy.deepcopy(base.client)
        settings["breaker"]["min_calls"] = 1_000_000_000
        settings["max_attempts_per_provider"] = 5
        if disabled:
            settings.update(retry_budget_ratio=100, retry_budget_max_tokens=1e9, retry_budget_min_per_s=1e6)
        run("budget_off" if disabled else "budget_on", replace(base, client=settings),
            ["G2_overload_storm", "G3_brownout"])
    settings = copy.deepcopy(base.client)
    settings.update(retry_budget_ratio=100, retry_budget_max_tokens=1e9, retry_budget_min_per_s=1e6)
    run("healthy_breaker_budget_off", replace(base, client=settings), ["G2_overload_storm", "G3_brownout"])
    run("timeout_30", replace(base, client={**base.client, "attempt_timeout_s": 30}),
        ["G4_slow_region", "G6_provider_outage"])
    run("no_tool_filter", base, ["G6_provider_outage"], unfiltered=True)
    for field, values in (("similarity_threshold", [0.8, 0.9, 0.97]), ("ttl_s", [30, 60, 300]),
                          ("max_stale_s", [0])):
        for value in values:
            run(f"cache_{field}_{value}", replace(cache_base, cache={**cache_base.cache, field: value}),
                ["C1_faq_outage"], "cached")
    run("cache_starter", replace(cache_base, cache={**cache_base.cache, "similarity_threshold": 0.8,
        "ttl_s": 600, "max_stale_s": 0}), ["C1_faq_outage"], "cached")
    Path("reports/experiments.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
