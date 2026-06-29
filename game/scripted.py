from __future__ import annotations

from typing import Any

from game.leader import LeaderDecision
from game.world import WorldState


class ScriptedLeaderController:
    """Small deterministic fallback leader for no-key playable demos."""

    def __init__(self, faction_id: str) -> None:
        self.faction_id = faction_id
        self.calls = 0
        self.chat_calls = 0
        self.last_task: str | None = None

    async def decide(
        self,
        world: WorldState,
        *,
        feedback: str | None = None,
    ) -> LeaderDecision:
        self.calls += 1
        self.last_task = f"scripted fallback for {self.faction_id} at tick {world.tick}"
        order = _first_safe_population_order(world, self.faction_id)
        if order is None:
            return LeaderDecision(
                turn_intent="稳住当前领地",
                strategy_summary="保持防守，等待新的机会。",
            )
        return LeaderDecision.from_mapping(
            {
                "turn_intent": "安排族人维持领地",
                "population_orders": [order],
                "strategy_summary": "将闲置人口投入当前最需要的工作。",
            }
        )

    async def chat_with_god(self, world: WorldState) -> str:
        self.chat_calls += 1
        messages = world.recent_god_chat(self.faction_id)
        if not messages:
            return "我会观察神迹，也会守住族人的利益。"
        return f"我听见了：{messages[-1].content}。若这能让族人存活，我们愿意谈。"


def _first_safe_population_order(
    world: WorldState,
    faction_id: str,
) -> dict[str, Any] | None:
    faction = world.factions[faction_id]
    for tile in sorted(
        world.faction_tiles(faction_id),
        key=lambda item: (item.population_of(faction_id), item.y, item.x),
        reverse=True,
    ):
        idle = tile.professions_of(faction_id).get("idle", 0)
        if idle <= 0:
            continue
        task = _best_task_for_tile(world, faction_id, tile)
        workers = min(idle, 3 if task != "train" else 5)
        if workers <= 0:
            continue
        return {
            "task": task,
            "target": {"x": tile.x, "y": tile.y},
            "workers": workers,
            "priority": 1,
        }
    return None


def _best_task_for_tile(world: WorldState, faction_id: str, tile) -> str:
    faction = world.factions[faction_id]
    population = world.total_population(faction_id)
    capacity = max(1, world.population_capacity(faction_id))
    soldiers = world.total_soldiers(faction_id)
    if population >= capacity - 3 and faction.resources.wood >= 10:
        return "build"
    if soldiers < max(4, population // 5):
        return "train"
    if tile.terrain == "forest":
        return "gather_wood"
    if tile.terrain == "hill":
        return "mine_stone"
    return "farm"
