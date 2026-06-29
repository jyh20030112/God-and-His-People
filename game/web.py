from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from simagentplg import ModelConfig
from game.engine import GameEngine
from game.leader import LLMLeaderController
from game.player import move_player, nearby_interactions, player_help, player_trade
from game.scripted import ScriptedLeaderController
from game.world import (
    DEFAULT_FACTIONS,
    RESOURCE_TYPES,
    WEATHER_TYPES,
    WorldState,
    create_default_world,
)

STATIC_DIR = Path(__file__).with_name("web_static")


class TickRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=50)


class GiveRequest(BaseModel):
    faction_id: str
    resource: str
    amount: int = Field(gt=0)


class WeatherRequest(BaseModel):
    x: int
    y: int
    weather: str
    duration: int | None = Field(default=None, ge=0, le=50)


class AnswerRequest(BaseModel):
    petition_id: int
    approve: bool


class GodChatRequest(BaseModel):
    faction_id: str
    message: str = Field(min_length=1)


class MoveRequest(BaseModel):
    direction: str


class TradeRequest(BaseModel):
    faction_id: str
    offer: dict[str, Any] = Field(default_factory=dict)
    request: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "low"


class HelpRequest(BaseModel):
    kind: str
    faction_id: str | None = None
    target: dict[str, int] | None = None
    x: int | None = None
    y: int | None = None
    resource: str | None = None
    amount: int | None = None
    weather: str | None = None
    duration: int | None = None


def create_engine(
    *,
    seed: int = 7,
    width: int = 32,
    height: int = 20,
    config: ModelConfig | None = None,
) -> GameEngine:
    world = create_default_world(width=width, height=height, seed=seed)
    if config is not None or _has_llm_env():
        model_config = config or ModelConfig.from_env()
        leaders = {
            faction_id: LLMLeaderController.create(
                config=model_config,
                faction_id=faction_id,
                world_provider=lambda world=world: world,
            )
            for faction_id in DEFAULT_FACTIONS
        }
    else:
        leaders = {
            faction_id: ScriptedLeaderController(faction_id)
            for faction_id in DEFAULT_FACTIONS
        }
    return GameEngine(
        world,
        leaders=leaders,
        strategy_interval=None,
        backup_strategy_interval=20,
        event_driven_strategy=True,
        log_ticks=False,
    )


def create_game_app(
    engine: GameEngine | None = None,
    *,
    auto_start: bool = True,
    tick_seconds: float = 1.2,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.auto_start:
            app.state.clock_task = asyncio.create_task(_world_clock(app))
        try:
            yield
        finally:
            task = app.state.clock_task
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                app.state.clock_task = None

    app = FastAPI(title="SimAgentPlg God Simulator", lifespan=lifespan)
    app.state.engine = engine or create_engine()
    app.state.tick_lock = asyncio.Lock()
    app.state.clock_task = None
    app.state.auto_start = auto_start
    app.state.tick_seconds = tick_seconds

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return serialize_player_state(app.state.engine.world)

    @app.get("/api/debug/state")
    async def debug_state() -> dict[str, Any]:
        return serialize_state(app.state.engine.world)

    @app.post("/api/tick")
    async def tick(request: TickRequest):
        if app.state.tick_lock.locked():
            return JSONResponse(
                {"error": "world is already advancing"},
                status_code=409,
            )
        async with app.state.tick_lock:
            await app.state.engine.tick(request.count)
            return serialize_state(app.state.engine.world)

    @app.post("/api/god/give")
    async def give(request: GiveRequest):
        try:
            app.state.engine.god.give_resource(
                request.faction_id,
                request.resource,
                request.amount,
            )
        except Exception as exc:
            return _error(exc)
        return serialize_player_state(app.state.engine.world)

    @app.post("/api/god/weather")
    async def weather(request: WeatherRequest):
        try:
            app.state.engine.god.set_weather(
                request.x,
                request.y,
                request.weather,
                request.duration,
            )
        except Exception as exc:
            return _error(exc)
        return serialize_player_state(app.state.engine.world)

    @app.post("/api/god/answer")
    async def answer(request: AnswerRequest):
        try:
            app.state.engine.god.answer_petition(
                request.petition_id,
                request.approve,
            )
        except Exception as exc:
            return _error(exc)
        return serialize_player_state(app.state.engine.world)

    @app.post("/api/god/chat")
    async def god_chat(request: GodChatRequest):
        if app.state.tick_lock.locked():
            return JSONResponse(
                {"error": "world is already advancing"},
                status_code=409,
            )
        async with app.state.tick_lock:
            world = app.state.engine.world
            try:
                faction = world.factions.get(request.faction_id)
                if faction is None:
                    raise ValueError(f"unknown faction {request.faction_id!r}")
                if faction.eliminated:
                    raise ValueError(f"faction {request.faction_id!r} is eliminated")
                message = request.message.strip()
                if not message:
                    raise ValueError("message must not be empty")
                controller = app.state.engine.leaders.get(request.faction_id)
                if controller is None:
                    raise ValueError(
                        f"missing leader controller for faction {request.faction_id}"
                    )
                chat_method = getattr(controller, "chat_with_god", None)
                if chat_method is None:
                    raise ValueError(
                        f"leader {request.faction_id} cannot answer god chat"
                    )
                world.add_god_chat_message(
                    faction_id=request.faction_id,
                    speaker="god",
                    content=message,
                )
                world.player.contacted_factions.add(request.faction_id)
                reply = await chat_method(world)
                world.add_god_chat_message(
                    faction_id=request.faction_id,
                    speaker="leader",
                    content=reply,
                )
            except Exception as exc:
                return _error(exc)
            return serialize_player_state(world)

    @app.post("/api/player/move")
    async def player_move(request: MoveRequest):
        if app.state.tick_lock.locked():
            return JSONResponse(
                {"error": "world is already advancing"},
                status_code=409,
            )
        async with app.state.tick_lock:
            try:
                move_player(app.state.engine.world, request.direction)
            except Exception as exc:
                return _error(exc)
            return serialize_player_state(app.state.engine.world)

    @app.post("/api/player/trade")
    async def trade(request: TradeRequest):
        if app.state.tick_lock.locked():
            return JSONResponse(
                {"error": "world is already advancing"},
                status_code=409,
            )
        async with app.state.tick_lock:
            try:
                result = player_trade(
                    app.state.engine.world,
                    {
                        "faction_id": request.faction_id,
                        "offer": request.offer,
                        "request": request.request,
                        "risk_level": request.risk_level,
                    },
                )
            except Exception as exc:
                return _error(exc)
            payload = serialize_player_state(app.state.engine.world)
            payload["last_interaction"] = result
            return payload

    @app.post("/api/player/help")
    async def help_faction(request: HelpRequest):
        if app.state.tick_lock.locked():
            return JSONResponse(
                {"error": "world is already advancing"},
                status_code=409,
            )
        async with app.state.tick_lock:
            try:
                player_help(
                    app.state.engine.world,
                    request.model_dump(exclude_none=True),
                )
            except Exception as exc:
                return _error(exc)
            return serialize_player_state(app.state.engine.world)

    return app


async def _world_clock(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(app.state.tick_seconds)
        if app.state.tick_lock.locked():
            continue
        async with app.state.tick_lock:
            await app.state.engine.tick()


def serialize_state(world: WorldState) -> dict[str, Any]:
    return {
        "tick": world.tick,
        "seed": world.seed,
        "width": world.width,
        "height": world.height,
        "paused": world.paused,
        "pause_reason": world.pause_reason,
        "player": world.player.as_dict(),
        "resources": list(RESOURCE_TYPES),
        "weather_types": list(WEATHER_TYPES),
        "tiles": [
            {
                "x": tile.x,
                "y": tile.y,
                "terrain": tile.terrain,
                "owner": tile.owner,
                "home_of": world.home_of_tile(tile.x, tile.y),
                "weather": tile.weather,
                "weather_duration": tile.weather_duration,
                "population": dict(tile.population),
                "soldiers": dict(tile.soldiers),
                "professions": {
                    faction_id: tile.professions_of(faction_id)
                    for faction_id in tile.population
                },
                "houses": tile.houses,
                "capacity": tile.capacity(),
                "protected": tile.protected,
            }
            for tile in world.tiles
        ],
        "factions": [
            {
                "faction_id": faction_id,
                "name": faction.name,
                "leader_name": faction.leader_name,
                "resources": faction.resources.as_dict(),
                "population": world.total_population(faction_id),
                "soldiers": world.total_soldiers(faction_id),
                "jobs": world.total_jobs(faction_id),
                "houses": world.total_houses(faction_id),
                "population_capacity": world.population_capacity(faction_id),
                "territory_count": len(world.faction_tiles(faction_id)),
                "home_tile": (
                    {"x": faction.home_tile[0], "y": faction.home_tile[1]}
                    if faction.home_tile is not None
                    else None
                ),
                "eliminated": faction.eliminated,
                "known_factions": sorted(faction.known_factions),
                "diplomacy": {
                    other_id: faction.relation_to(other_id)
                    for other_id in sorted(faction.known_factions)
                    if other_id != faction_id
                },
                "last_plan_snapshot": dict(faction.last_plan_snapshot),
                "leader_memory": dict(faction.leader_memory),
                "leader_context_window_count": len(faction.leader_context_window),
            }
            for faction_id, faction in sorted(world.factions.items())
        ],
        "petitions": [
            petition.as_dict()
            for petition in world.petitions
            if petition.status == "pending"
        ],
        "god_chats": [
            message.as_dict()
            for message in world.god_chats[-80:]
        ],
        "events": [
            event.as_dict()
            for event in world.events[-80:]
        ],
    }


def serialize_player_state(world: WorldState) -> dict[str, Any]:
    visible = world.player_visible_tiles()
    known_factions = _player_known_factions(world, visible)
    visible_faction_ids = {item["faction_id"] for item in known_factions}
    return {
        "tick": world.tick,
        "seed": world.seed,
        "width": world.width,
        "height": world.height,
        "paused": world.paused,
        "pause_reason": world.pause_reason,
        "resources": list(RESOURCE_TYPES),
        "weather_types": list(WEATHER_TYPES),
        "player": world.player.as_dict(),
        "visible_bounds": _visible_bounds(visible),
        "tiles": [
            _tile_payload(world, world.tile_at(x, y), visible=True)
            for x, y in sorted(visible, key=lambda item: (item[1], item[0]))
        ],
        "known_factions": known_factions,
        "nearby_interactions": nearby_interactions(world),
        "petitions": [
            petition.as_dict()
            for petition in world.petitions
            if petition.status == "pending"
            and petition.faction_id in visible_faction_ids
        ],
        "god_chats": [
            message.as_dict()
            for message in world.god_chats[-80:]
            if message.faction_id in visible_faction_ids
        ],
        "events": [
            event.as_dict()
            for event in world.events[-80:]
            if _event_is_player_visible(event, visible_faction_ids)
        ],
    }


def _tile_payload(world: WorldState, tile, *, visible: bool) -> dict[str, Any]:
    return {
        "x": tile.x,
        "y": tile.y,
        "visible": visible,
        "terrain": tile.terrain,
        "owner": tile.owner,
        "home_of": world.home_of_tile(tile.x, tile.y),
        "weather": tile.weather,
        "weather_duration": tile.weather_duration,
        "population": dict(tile.population),
        "soldiers": dict(tile.soldiers),
        "professions": {
            faction_id: tile.professions_of(faction_id)
            for faction_id in tile.population
        },
        "houses": tile.houses,
        "capacity": tile.capacity(),
        "protected": tile.protected,
    }


def _player_known_factions(
    world: WorldState,
    visible: set[tuple[int, int]],
) -> list[dict[str, Any]]:
    faction_ids = {
        world.tile_at(x, y).owner
        for x, y in visible
        if world.tile_at(x, y).owner is not None
    }
    faction_ids.update(world.player.contacted_factions)
    summaries = []
    for faction_id in sorted(faction_id for faction_id in faction_ids if faction_id in world.factions):
        faction = world.factions[faction_id]
        visible_tiles = [
            world.tile_at(x, y)
            for x, y in visible
            if world.tile_at(x, y).owner == faction_id
        ]
        summaries.append(
            {
                "faction_id": faction_id,
                "name": faction.name,
                "leader_name": faction.leader_name,
                "contacted": faction_id in world.player.contacted_factions,
                "visible_territory_count": len(visible_tiles),
                "visible_population": sum(tile.population_of(faction_id) for tile in visible_tiles),
                "visible_soldiers": sum(tile.soldiers_of(faction_id) for tile in visible_tiles),
                "eliminated": faction.eliminated,
                "last_plan_summary": faction.last_plan_snapshot.get("strategy_summary", ""),
            }
        )
    return summaries


def _visible_bounds(visible: set[tuple[int, int]]) -> dict[str, int]:
    if not visible:
        return {"min_x": 0, "max_x": 0, "min_y": 0, "max_y": 0}
    xs = [x for x, _y in visible]
    ys = [y for _x, y in visible]
    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
    }


def _event_is_player_visible(event, visible_faction_ids: set[str]) -> bool:
    if event.kind in {"world", "tick", "player", "player_trade", "player_help"}:
        return True
    if event.faction_id is None:
        return True
    return event.faction_id in visible_faction_ids


def _has_llm_env() -> bool:
    enabled = os.getenv("USE_LLM_LEADERS", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return False
    return bool(os.getenv("MODEL_API_KEY") or os.getenv("OPENAI_API_KEY"))


def _error(exc: Exception) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=400)
