import logging
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from src.config import get_settings
    from src.simulator.scenarios import list_scenarios, load_scenario
    from src.vision.seed import _resolve_data_path
except ImportError:
    from config import get_settings
    from simulator.scenarios import list_scenarios, load_scenario
    from vision.seed import _resolve_data_path

logger = logging.getLogger("office_vision.api.simulator")

router = APIRouter(prefix="/api/simulator", tags=["Scenario Simulator"])


class ScenarioSummaryResponse(BaseModel):
    name: str
    description: str
    step_count: int


class ScenarioRunResultResponse(BaseModel):
    scenario_name: str
    steps_processed: int
    events_emitted: int
    event_type_counts: Dict[str, int] = Field(default_factory=dict)
    incidents: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


@router.get("/scenarios", response_model=List[ScenarioSummaryResponse])
async def get_scenarios():
    """List all available scenario definitions."""
    settings = get_settings()
    scenarios = list_scenarios(settings.simulator_scenarios_dir)
    return [
        ScenarioSummaryResponse(
            name=s.name,
            description=s.description,
            step_count=len(s.steps),
        )
        for s in scenarios
    ]


@router.post("/scenarios/{name}/run", response_model=ScenarioRunResultResponse)
async def run_scenario(name: str, request: Request):
    """Execute a scenario synchronously against the active ObservationPipeline."""
    runner = getattr(request.app.state, "scenario_runner", None)
    if runner is None:
        raise HTTPException(
            status_code=503,
            detail="Scenario runner is not initialized.",
        )

    settings = get_settings()
    scenarios_dir = _resolve_data_path(settings.simulator_scenarios_dir, settings.simulator_scenarios_dir)
    scenario_path = scenarios_dir / f"{name}.yaml"

    if not scenario_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Scenario '{name}' not found at {scenario_path}.",
        )

    try:
        scenario = load_scenario(scenario_path)
    except Exception as e:
        logger.error("Failed to load scenario '%s': %s", name, e)
        raise HTTPException(status_code=500, detail=f"Failed to load scenario: {e}")

    try:
        result = runner.run(scenario)
    except Exception as e:
        logger.error("Error executing scenario '%s': %s", name, e)
        raise HTTPException(status_code=500, detail=f"Scenario execution error: {e}")

    return ScenarioRunResultResponse(
        scenario_name=result.scenario_name,
        steps_processed=result.steps_processed,
        events_emitted=result.events_emitted,
        event_type_counts=result.event_type_counts,
        incidents=result.incidents,
        errors=result.errors,
    )
