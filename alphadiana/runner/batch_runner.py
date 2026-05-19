"""BatchRunner — run multiple experiment configs sequentially or in parallel."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from alphadiana.runner.runner import Runner

if TYPE_CHECKING:
    from alphadiana.config.experiment_config import ExperimentConfig

logger = logging.getLogger(__name__)


class BatchRunner:
    """Run a list of experiment configs, either sequentially or in parallel."""

    def __init__(
        self,
        configs: list[ExperimentConfig],
        parallel: bool = False,
        max_workers: int | None = None,
    ) -> None:
        self._configs = configs
        self._parallel = parallel
        self._max_workers = max_workers or len(configs)

    def run(self) -> list:
        if self._parallel:
            return self._run_parallel()
        return self._run_sequential()

    def _run_sequential(self) -> list:
        summaries = []
        for config in self._configs:
            runner = None
            try:
                runner = Runner(config)
                runner.setup()
                summary = runner.run()
                summaries.append(summary)
            except Exception as exc:
                logger.error("Run %s failed: %s", config.run_id, exc)
                summaries.append(None)
            finally:
                if runner is not None:
                    runner.teardown()
        return summaries

    def _run_parallel(self) -> list:
        summaries = [None] * len(self._configs)
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            future_to_idx = {}
            for i, config in enumerate(self._configs):
                future = pool.submit(self._run_one, config)
                future_to_idx[future] = i
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    summaries[idx] = future.result()
                except Exception as exc:
                    logger.error("Run %s failed: %s", self._configs[idx].run_id, exc)
        return summaries

    @staticmethod
    def _run_one(config: ExperimentConfig):
        runner = Runner(config)
        try:
            runner.setup()
            return runner.run()
        finally:
            runner.teardown()
