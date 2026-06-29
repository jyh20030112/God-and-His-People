from __future__ import annotations

from typing import Any

from game.world import RESOURCE_TYPES, WEATHER_TYPES, WorldState

DIRECTIONS = {
    "north": (0, -1),
    "south": (0, 1),
    "west": (-1, 0),
    "east": (1, 0),
}

HELP_POWER_COST = {
    "weather": 18,
    "protection": 25,
}

RISK_REWARDS = {
    "low": 8,
    "medium": 14,
    "high": 24,
}


def move_player(world: WorldState, direction: str) -> None:
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown direction {direction!r}")
    dx, dy = DIRECTIONS[direction]
    nx = world.player.x + dx
    ny = world.player.y + dy
    if not world.in_bounds(nx, ny):
        raise ValueError("cannot move outside the world")
    tile = world.tile_at(nx, ny)
    if not tile.is_passable():
        raise ValueError("cannot move into water or mountain")

    world.player.x = nx
    world.player.y = ny
    if world.player.divine_power > 0:
        world.player.divine_power -= 1
    newly_seen = world.reveal_player_area()
    contacts = world.update_player_contacts()
    if newly_seen:
        world.add_event(
            "player",
            f"Player discovered {len(newly_seen)} new tiles near ({nx}, {ny})",
        )
    for faction_id in contacts:
        world.add_event(
            "player",
            f"Player contacted {faction_id}",
            faction_id=faction_id,
        )


def player_help(world: WorldState, payload: dict[str, Any]) -> None:
    kind = str(payload.get("kind", "")).strip()
    if kind == "resources":
        _help_resources(world, payload)
    elif kind == "weather":
        _help_weather(world, payload)
    elif kind == "protection":
        _help_protection(world, payload)
    else:
        raise ValueError(f"unsupported help kind {kind!r}")


def player_trade(world: WorldState, payload: dict[str, Any]) -> dict[str, Any]:
    faction_id = str(payload.get("faction_id", "")).strip()
    if not _can_interact_with_faction(world, faction_id):
        raise ValueError(f"faction {faction_id!r} is not reachable")

    risk_level = str(payload.get("risk_level", "low")).strip() or "low"
    if risk_level not in RISK_REWARDS:
        raise ValueError(f"unknown risk level {risk_level!r}")

    offer = _as_dict(payload.get("offer"))
    request = _as_dict(payload.get("request"))
    _pay_offer(world, offer, risk_level)
    success = _trade_succeeds(world, faction_id, risk_level, offer)
    if success:
        reward = RISK_REWARDS[risk_level]
        world.player.godhood_progress = min(100, world.player.godhood_progress + reward)
        _grant_trade_request(world, faction_id, request, risk_level)
        world.add_event(
            "player_trade",
            (
                f"Player struck a {risk_level} trade with {faction_id}; "
                f"godhood +{reward}"
            ),
            faction_id=faction_id,
        )
    else:
        penalty = 5 if risk_level == "medium" else 12
        world.player.divine_power = max(0, world.player.divine_power - penalty)
        world.add_event(
            "player_trade",
            (
                f"Player failed a {risk_level} trade with {faction_id}; "
                f"divine_power -{penalty}"
            ),
            faction_id=faction_id,
        )
    return {"success": success, "risk_level": risk_level}


def nearby_interactions(world: WorldState) -> list[dict[str, Any]]:
    visible = world.player_visible_tiles()
    interactions: dict[str, dict[str, Any]] = {}
    for x, y in visible:
        tile = world.tile_at(x, y)
        if tile.owner is None:
            continue
        interactions[tile.owner] = {
            "faction_id": tile.owner,
            "name": world.factions[tile.owner].name,
            "leader_name": world.factions[tile.owner].leader_name,
            "relation": "contacted" if tile.owner in world.player.contacted_factions else "visible",
            "distance": abs(world.player.x - x) + abs(world.player.y - y),
            "can_trade": tile.owner in world.player.contacted_factions,
            "can_help": tile.owner in world.player.contacted_factions,
        }
    return sorted(interactions.values(), key=lambda item: (item["distance"], item["faction_id"]))


def _help_resources(world: WorldState, payload: dict[str, Any]) -> None:
    faction_id = str(payload.get("faction_id", "")).strip()
    if not _can_interact_with_faction(world, faction_id):
        raise ValueError(f"faction {faction_id!r} is not reachable")
    resource = str(payload.get("resource", "")).strip()
    amount = int(payload.get("amount", 0))
    if resource not in RESOURCE_TYPES:
        raise ValueError(f"unknown resource {resource!r}")
    if amount <= 0:
        raise ValueError("amount must be positive")
    if world.player.inventory.amount(resource) < amount:
        raise ValueError(f"not enough carried {resource}")
    world.player.inventory.remove(resource, amount)
    world.factions[faction_id].resources.add(resource, amount)
    world.player.godhood_progress = min(100, world.player.godhood_progress + max(1, amount // 10))
    world.add_event(
        "player_help",
        f"Player gave {amount} {resource} to {faction_id}",
        faction_id=faction_id,
    )


def _help_weather(world: WorldState, payload: dict[str, Any]) -> None:
    target = _target(payload)
    if target is None:
        raise ValueError("weather help requires target x and y")
    x, y = target
    if not world.player_can_see(x, y):
        raise ValueError("weather target is outside player vision")
    weather = str(payload.get("weather", "")).strip()
    if weather not in WEATHER_TYPES:
        raise ValueError(f"unknown weather {weather!r}")
    duration = max(0, int(payload.get("duration", 5)))
    cost = HELP_POWER_COST["weather"] + max(0, duration - 5)
    _spend_power(world, cost)
    tile = world.tile_at(x, y)
    tile.weather = weather
    tile.weather_duration = duration
    world.add_event(
        "player_help",
        f"Player changed weather at ({x}, {y}) to {weather} for {duration} ticks",
        faction_id=tile.owner,
    )


def _help_protection(world: WorldState, payload: dict[str, Any]) -> None:
    target = _target(payload)
    if target is None:
        raise ValueError("protection help requires target x and y")
    x, y = target
    if not world.player_can_see(x, y):
        raise ValueError("protection target is outside player vision")
    _spend_power(world, HELP_POWER_COST["protection"])
    tile = world.tile_at(x, y)
    tile.protected = True
    world.add_event(
        "player_help",
        f"Player protected tile ({x}, {y})",
        faction_id=tile.owner,
    )


def _pay_offer(world: WorldState, offer: dict[str, Any], risk_level: str) -> None:
    resource = str(offer.get("resource", "")).strip()
    amount = int(offer.get("amount", 0) or 0)
    divine_power = int(offer.get("divine_power", 0) or 0)
    if resource:
        if resource not in RESOURCE_TYPES:
            raise ValueError(f"unknown offer resource {resource!r}")
        if amount <= 0:
            raise ValueError("resource offer amount must be positive")
        if world.player.inventory.amount(resource) < amount:
            raise ValueError(f"not enough carried {resource}")
        world.player.inventory.remove(resource, amount)
    if divine_power > 0:
        _spend_power(world, divine_power)
    if not resource and divine_power <= 0 and risk_level != "high":
        raise ValueError("trade needs a resource or divine_power offer")


def _grant_trade_request(
    world: WorldState,
    faction_id: str,
    request: dict[str, Any],
    risk_level: str,
) -> None:
    kind = str(request.get("kind", "faith")).strip() or "faith"
    if kind == "resource":
        resource = str(request.get("resource", "food")).strip()
        amount = max(1, int(request.get("amount", 10)))
        if resource not in RESOURCE_TYPES:
            raise ValueError(f"unknown request resource {resource!r}")
        available = world.factions[faction_id].resources.amount(resource)
        granted = min(available, amount)
        if granted > 0:
            world.factions[faction_id].resources.remove(resource, granted)
            world.player.inventory.add(resource, granted)
    elif kind == "intel":
        home = world.factions[faction_id].home_tile
        if home is not None:
            _reveal_around(world, home, radius=3 if risk_level == "high" else 2)
    elif kind == "peace":
        for other_id in world.factions:
            if other_id != faction_id and world.factions[faction_id].relation_to(other_id) == "war":
                world.factions[faction_id].diplomacy[other_id] = "neutral"
                world.factions[other_id].diplomacy[faction_id] = "neutral"


def _trade_succeeds(
    world: WorldState,
    faction_id: str,
    risk_level: str,
    offer: dict[str, Any],
) -> bool:
    if risk_level == "low":
        return True
    offered_amount = int(offer.get("amount", 0) or 0) + int(offer.get("divine_power", 0) or 0)
    if risk_level == "medium":
        return offered_amount >= 10 or world.player.divine_power >= 40
    stable_seed = sum(ord(char) for char in faction_id)
    return (world.seed + world.tick + stable_seed + offered_amount) % 3 != 0


def _can_interact_with_faction(world: WorldState, faction_id: str) -> bool:
    if faction_id not in world.factions or world.factions[faction_id].eliminated:
        return False
    if faction_id in world.player.contacted_factions:
        return True
    return any(world.tile_at(x, y).owner == faction_id for x, y in world.player_visible_tiles())


def _spend_power(world: WorldState, amount: int) -> None:
    if amount <= 0:
        return
    if world.player.divine_power < amount:
        raise ValueError("not enough divine_power")
    world.player.divine_power -= amount


def _target(payload: dict[str, Any]) -> tuple[int, int] | None:
    target = payload.get("target")
    if isinstance(target, dict):
        try:
            return (int(target["x"]), int(target["y"]))
        except (KeyError, TypeError, ValueError):
            return None
    try:
        return (int(payload["x"]), int(payload["y"]))
    except (KeyError, TypeError, ValueError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _reveal_around(
    world: WorldState,
    center: tuple[int, int],
    *,
    radius: int,
) -> None:
    cx, cy = center
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            if world.in_bounds(x, y) and abs(cx - x) + abs(cy - y) <= radius:
                world.player.discovered_tiles.add((x, y))
