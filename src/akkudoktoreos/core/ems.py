import asyncio
import gc
import traceback
from asyncio import Lock, get_running_loop
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from functools import partial
from typing import ClassVar, Optional

from loguru import logger
from pydantic import computed_field

from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import (
    AdapterMixin,
    ConfigMixin,
    PredictionMixin,
    SingletonMixin,
)
from akkudoktoreos.core.emplan import EnergyManagementPlan
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

# The executor to execute the CPU heavy energy management run
executor = ThreadPoolExecutor(max_workers=1)


class EnergyManagementStage(StrEnum):
    """Enumeration of the main stages in the energy management lifecycle."""

    IDLE = "IDLE"
    DATA_ACQUISITION = "DATA_AQUISITION"
    FORECAST_RETRIEVAL = "FORECAST_RETRIEVAL"
    OPTIMIZATION = "OPTIMIZATION"
    CONTROL_DISPATCH = "CONTROL_DISPATCH"


async def ems_manage_energy() -> None:
    """Repeating task for managing energy.

    This task should be executed by the server regularly
    to ensure proper energy management.
    """
    await EnergyManagement().run(respect_interval=True)


class EnergyManagement(
    SingletonMixin, ConfigMixin, PredictionMixin, AdapterMixin, PydanticBaseModel
):
    """Energy management."""

    # Start datetime.
    _start_datetime: ClassVar[Optional[DateTime]] = None

    # last run datetime. Used by energy management task
    _last_run_datetime: ClassVar[Optional[DateTime]] = None

    # Current energy management stage
    _stage: ClassVar[EnergyManagementStage] = EnergyManagementStage.IDLE

    # energy management plan of latest energy management run with optimization
    _plan: ClassVar[Optional[EnergyManagementPlan]] = None

    # opimization solution of the latest energy management run
    _optimization_solution: ClassVar[Optional[OptimizationSolution]] = None

    # Solution of the genetic algorithm of latest energy management run with optimization
    # For classic API
    _genetic_solution: ClassVar[Optional[GeneticSolution]] = None

    # energy management lock (for energy management run)
    _run_lock: ClassVar[Lock] = Lock()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def start_datetime(self) -> DateTime:
        """The starting datetime of the current or latest energy management."""
        if EnergyManagement._start_datetime is None:
            EnergyManagement.set_start_datetime()
        return EnergyManagement._start_datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def last_run_datetime(self) -> Optional[DateTime]:
        """The datetime the last energy management was run."""
        return EnergyManagement._last_run_datetime

    @classmethod
    def set_start_datetime(cls, start_datetime: Optional[DateTime] = None) -> DateTime:
        """Set the start datetime for the next energy management run.

        If no datetime is provided, the current datetime is used.

        The start datetime is always rounded down to the nearest hour
        (i.e., setting minutes, seconds, and microseconds to zero).

        Args:
            start_datetime (Optional[DateTime]): The datetime to set as the start.
                If None, the current datetime is used.

        Returns:
            DateTime: The adjusted start datetime.
        """
        if start_datetime is None:
            start_datetime = to_datetime()
        cls._start_datetime = start_datetime.set(minute=0, second=0, microsecond=0)
        return cls._start_datetime

    @classmethod
    def stage(cls) -> EnergyManagementStage:
        """Get the the stage of the energy management.

        Returns:
            EnergyManagementStage: The current stage of energy management.
        """
        return cls._stage

    @classmethod
    def plan(cls) -> Optional[EnergyManagementPlan]:
        """Get the latest energy management plan.

        Returns:
            Optional[EnergyManagementPlan]: The latest energy management plan or None.
        """
        return cls._plan

    @classmethod
    def optimization_solution(cls) -> Optional[OptimizationSolution]:
        """Get the latest optimization solution.

        Returns:
            Optional[OptimizationSolution]: The latest optimization solution.
        """
        return cls._optimization_solution

    @classmethod
    def genetic_solution(cls) -> Optional[GeneticSolution]:
        """Get the latest solution of the genetic algorithm.

        Returns:
            Optional[GeneticSolution]: The latest solution of the genetic algorithm.
        """
        return cls._genetic_solution

    @classmethod
    def _debug_solution_summary(cls) -> dict:
        """Return a compact summary for debug logs.

        Avoid logging full optimization objects because they can become very large
        and cause excessive logging overhead on long-running systems.
        """
        optimization_solution = cls._optimization_solution
        plan = cls._plan
        summary: dict = {
            "start_datetime": str(cls._start_datetime) if cls._start_datetime else None,
            "last_run_datetime": (
                str(cls._last_run_datetime) if cls._last_run_datetime else None
            ),
            "optimization_id": getattr(optimization_solution, "id", None),
            "optimization_rows": 0,
            "plan_rows": 0,
            "plan_first_timestamp": None,
            "plan_last_timestamp": None,
        }

        optimization_data = getattr(optimization_solution, "data", None)
        if isinstance(optimization_data, dict):
            summary["optimization_rows"] = len(optimization_data)

        plan_data = getattr(plan, "data", None)
        if isinstance(plan_data, dict) and plan_data:
            sorted_timestamps = sorted(plan_data.keys())
            summary["plan_rows"] = len(sorted_timestamps)
            summary["plan_first_timestamp"] = sorted_timestamps[0]
            summary["plan_last_timestamp"] = sorted_timestamps[-1]

        return summary

    @classmethod
    def _run(
        cls,
        start_datetime: DateTime,
        mode: EnergyManagementMode,
        genetic_parameters: Optional[GeneticOptimizationParameters] = None,
        genetic_individuals: Optional[int] = None,
        genetic_seed: Optional[int] = None,
        force_enable: Optional[bool] = False,
        force_update: Optional[bool] = False,
    ) -> None:
        """Run the energy management.

        This method initializes the energy management run by setting its

        start datetime, updating predictions, and optionally starting
        optimization depending on the selected mode or configuration.

        Args:
            start_datetime (DateTime): The starting timestamp of the energy management run.
            mode (EnergyManagementMode): The management mode to use. Must be one of:
                - "OPTIMIZATION": Runs the optimization process.
                - "PREDICTION": Updates the forecast without optimization.
                - "DISABLED": Does not run.
            genetic_parameters (GeneticOptimizationParameters, optional): The
                parameter set for the genetic algorithm. If not provided, it will
                be constructed based on the current configuration and predictions.
            genetic_individuals (int, optional): The number of individuals for the
                genetic algorithm. Defaults to the algorithm's internal default (400)
                if not specified.
            genetic_seed (int, optional): The seed for the genetic algorithm. Defaults
                to the algorithm's internal random seed if not specified.
            force_enable (bool, optional): If True, bypasses any disabled state
                to force the update process. This is mostly applicable to
                prediction providers.
            force_update (bool, optional): If True, forces data to be refreshed
                even if a cached version is still valid.

        Returns:
            None
        """
        # Ensure there is only one optimization/ energy management run at a time
        if not mode in EnergyManagementMode._value2member_map_:
            raise ValueError(f"Unknown energy management mode {mode}.")
        if mode == EnergyManagementMode.DISABLED:
            return

        logger.info("Starting energy management run.")

        cls._stage = EnergyManagementStage.DATA_ACQUISITION

        # Remember/ set the start datetime of this energy management run.
        # None leads
        cls.set_start_datetime(start_datetime)

        # Throw away any memory cached results of the last energy management run.
        CacheEnergyManagementStore().clear()

        # Do data aquisition by adapters
        try:
            cls.adapter.update_data(force_enable)
        except Exception as e:
            trace = "".join(traceback.TracebackException.from_exception(e).format())
            error_msg = f"Adapter update failed - phase {cls._stage}:\n{e}\n{trace}"
            logger.error(error_msg)

        cls._stage = EnergyManagementStage.FORECAST_RETRIEVAL

        if mode == EnergyManagementMode.PREDICTION:
            # Update the predictions
            cls.prediction.update_data(force_enable=force_enable, force_update=force_update)
            # Remember energy run datetime.
            EnergyManagement._last_run_datetime = to_datetime()
            logger.info("Energy management run done (predictions updated)")
            cls._stage = EnergyManagementStage.IDLE
            return

        # Prepare optimization parameters
        # This also creates default configurations for missing values and updates the predictions
        logger.info(
            "Starting energy management prediction update and optimzation parameter preparation."
        )
        if genetic_parameters is None:
            genetic_parameters = GeneticOptimizationParameters.prepare()

        if not genetic_parameters:
            logger.error(
                "Energy management run canceled. Could not prepare optimisation parameters."
            )
            cls._stage = EnergyManagementStage.IDLE
            return

        cls._stage = EnergyManagementStage.OPTIMIZATION
        logger.info("Starting energy management optimization.")

        # Take values from config if not given
        if genetic_individuals is None:
            genetic_individuals = cls.config.optimization.genetic.individuals
        if genetic_seed is None:
            genetic_seed = cls.config.optimization.genetic.seed

        if cls._start_datetime is None:  # Make mypy happy - already set by us
            raise RuntimeError("Start datetime not set.")

        # GC strategy for DEAP optimization on memory-constrained systems:
        # 1. Collect garbage before optimization to start with minimal memory footprint
        # 2. Suppress expensive gen-2 collections during optimization (gen-0/gen-1 still run)
        # 3. After optimization, restore thresholds and collect freed DEAP objects
        gc.collect()
        original_thresholds = gc.get_threshold()
        # Allow gen-0/gen-1 (fast, short-lived objects) but prevent gen-2 (full sweep)
        gc.set_threshold(original_thresholds[0], original_thresholds[1], 999_999_999)
        try:
            optimization = GeneticOptimization(
                verbose=bool(cls.config.server.verbose),
                fixed_seed=genetic_seed,
            )
            solution = optimization.optimierung_ems(
                start_hour=cls._start_datetime.hour,
                parameters=genetic_parameters,
                ngen=None,
            )
        except:
            logger.exception("Energy management optimization failed.")
            cls._stage = EnergyManagementStage.IDLE
            return
        finally:
            # Restore original GC thresholds and collect DEAP cyclic references.
            gc.set_threshold(*original_thresholds)
            del optimization
            gc.collect()

        cls._stage = EnergyManagementStage.CONTROL_DISPATCH

        # Make genetic solution public
        cls._genetic_solution = solution

        # Make optimization solution public
        cls._optimization_solution = solution.optimization_solution()

        # Make plan public
        cls._plan = solution.energy_management_plan()

        logger.debug("Energy management optimization summary: {}", cls._debug_solution_summary())
        logger.info("Energy management run done (optimization updated)")

        # Do control dispatch by adapters
        try:
            cls.adapter.update_data(force_enable)
        except Exception as e:
            trace = "".join(traceback.TracebackException.from_exception(e).format())
            error_msg = f"Adapter update failed - phase {cls._stage}:\n{e}\n{trace}"
            logger.error(error_msg)

        # Remember energy run datetime.
        EnergyManagement._last_run_datetime = to_datetime()

        # energy management run finished
        cls._stage = EnergyManagementStage.IDLE

    async def run(
        self,
        start_datetime: Optional[DateTime] = None,
        mode: Optional[EnergyManagementMode] = None,
        genetic_parameters: Optional[GeneticOptimizationParameters] = None,
        genetic_individuals: Optional[int] = None,
        genetic_seed: Optional[int] = None,
        force_enable: Optional[bool] = False,
        force_update: Optional[bool] = False,
        respect_interval: Optional[bool] = False,
    ) -> None:
        """Run the energy management.

        This method initializes the energy management run by setting its
        start datetime, updating predictions, and optionally starting
        optimization depending on the selected mode or configuration.

        Args:
            start_datetime (DateTime, optional): The starting timestamp
                of the energy management run. Defaults to the current datetime
                if not provided.
            mode (EnergyManagementMode, optional): The management mode to use. Must be one of:
                - "OPTIMIZATION": Runs the optimization process.
                - "PREDICTION": Updates the forecast without optimization.

                Defaults to the mode defined in the current configuration.
            genetic_parameters (GeneticOptimizationParameters, optional): The
                parameter set for the genetic algorithm. If not provided, it will
                be constructed based on the current configuration and predictions.
            genetic_individuals (int, optional): The number of individuals for the
                genetic algorithm. Defaults to the algorithm's internal default (400)
                if not specified.
            genetic_seed (int, optional): The seed for the genetic algorithm. Defaults
                to the algorithm's internal random seed if not specified.
            force_enable (bool, optional): If True, bypasses any disabled state
                to force the update process. This is mostly applicable to
                prediction providers.
            force_update (bool, optional): If True, forces data to be refreshed
                even if a cached version is still valid.
            respect_interval (bool, optional): If True, skip this run when the
                configured EMS interval has not elapsed since the last completed run.
                Intended for scheduled/background runs.

        Returns:
            None
        """
        async with self._run_lock:
            if respect_interval:
                last_run_datetime = EnergyManagement._last_run_datetime
                interval_sec = float(self.config.ems.interval)
                if last_run_datetime is not None:
                    elapsed_sec = (to_datetime() - last_run_datetime).total_seconds()
                    if elapsed_sec < interval_sec:
                        logger.debug(
                            "Skipping scheduled energy management run: elapsed={}s, interval={}s.",
                            int(elapsed_sec),
                            int(interval_sec),
                        )
                        return

            loop = get_running_loop()
            # Create a partial function with parameters "baked in"
            if start_datetime is None:
                start_datetime = to_datetime()
            if mode is None:
                mode = self.config.ems.mode
            func = partial(
                EnergyManagement._run,
                start_datetime=start_datetime,
                mode=mode,
                genetic_parameters=genetic_parameters,
                genetic_individuals=genetic_individuals,
                genetic_seed=genetic_seed,
                force_enable=force_enable,
                force_update=force_update,
            )
            # Run optimization in background thread with timeout to prevent
            # the server from becoming permanently unresponsive.
            timeout_sec = max(float(self.config.ems.interval) * 4, 300.0)
            try:
                await asyncio.wait_for(loop.run_in_executor(executor, func), timeout=timeout_sec)
            except asyncio.TimeoutError:
                logger.error(
                    "Energy management run timed out after {}s — skipping this run.",
                    int(timeout_sec),
                )
                EnergyManagement._stage = EnergyManagementStage.IDLE
