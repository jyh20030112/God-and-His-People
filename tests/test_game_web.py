import asyncio
import unittest
from unittest.mock import patch

import httpx

from game import GameEngine, LeaderDecision, ScriptedLeaderController, create_default_world
from game.web import STATIC_DIR, create_engine, create_game_app


class WebLeader:
    def __init__(self, faction_id: str) -> None:
        self.faction_id = faction_id
        self.calls = 0
        self.chat_calls = 0

    async def decide(
        self,
        world,
        *,
        feedback: str | None = None,
    ) -> LeaderDecision:
        self.calls += 1
        return LeaderDecision(turn_intent="hold position")

    async def chat_with_god(self, world) -> str:
        self.chat_calls += 1
        latest = world.recent_god_chat(self.faction_id)[-1]
        return f"谨遵神谕：{latest.content}"


class AppClient:
    def __init__(self, app) -> None:
        self.app = app

    def get(self, path: str):
        return asyncio.run(self._request("GET", path))

    def post(self, path: str, json: dict | None = None):
        return asyncio.run(self._request("POST", path, json=json))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
    ):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, json=json)


class GameWebTests(unittest.TestCase):
    def make_client(self, *, strategy_interval: int = 5) -> AppClient:
        world = create_default_world(width=12, height=8, seed=9)
        leaders = {
            faction_id: WebLeader(faction_id)
            for faction_id in world.factions
        }
        engine = GameEngine(
            world,
            leaders=leaders,
            strategy_interval=strategy_interval,
        )
        return AppClient(create_game_app(engine, auto_start=False))

    def test_index_page_is_localized_to_chinese(self) -> None:
        text = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn("带回神性", text)
        self.assertIn("WASD / 方向按钮移动", text)
        self.assertIn("高风险交易", text)
        self.assertIn("有限援助", text)
        self.assertNotIn("推进 1 刻", text)
        self.assertNotIn("推进 5 刻", text)
        self.assertNotIn("claimFactionSelect", text)
        self.assertNotIn("划给领土", text)
        self.assertNotIn("/api/god/claim", text)
        self.assertNotIn("/api/debug/state", text)
        self.assertIn("tradeFaction", text)
        self.assertIn("helpKind", text)

    def test_web_engine_defaults_to_scripted_leaders_without_explicit_llm_mode(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "MODEL_API_KEY": "present-but-not-enabled",
                "OPENAI_API_KEY": "present-but-not-enabled",
            },
            clear=True,
        ):
            engine = create_engine(width=12, height=8, seed=3)

        self.assertTrue(
            all(
                isinstance(leader, ScriptedLeaderController)
                for leader in engine.leaders.values()
            )
        )

    def test_state_endpoint_returns_local_explorer_world(self) -> None:
        client = self.make_client()

        response = client.get("/api/state")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["width"], 12)
        self.assertEqual(payload["height"], 8)
        self.assertEqual(payload["tick"], 0)
        self.assertIn("player", payload)
        self.assertIn("visible_bounds", payload)
        self.assertIn("known_factions", payload)
        self.assertIn("nearby_interactions", payload)
        self.assertNotIn("factions", payload)
        self.assertLess(len(payload["tiles"]), 96)
        self.assertTrue(all(tile["visible"] for tile in payload["tiles"]))
        self.assertIn("professions", payload["tiles"][0])
        self.assertIn("houses", payload["tiles"][0])
        self.assertIn("capacity", payload["tiles"][0])
        self.assertIn("home_of", payload["tiles"][0])
        self.assertIn("weather_duration", payload["tiles"][0])
        player = payload["player"]
        self.assertIn("divine_power", player)
        self.assertIn("inventory", player)
        self.assertIn("godhood_progress", player)
        self.assertIn("discovered_tiles_count", player)

    def test_debug_state_endpoint_exposes_full_world_contract(self) -> None:
        client = self.make_client()
        engine = client.app.state.engine
        engine.world.add_petition(
            faction_id="human",
            kind="weather",
            request={"x": 1, "y": 2, "weather": "rain"},
            reason="dry fields",
            urgency="high",
        )
        engine.world.add_god_chat_message(
            faction_id="human",
            speaker="god",
            content="守住东线。",
        )
        engine.world.add_god_chat_message(
            faction_id="human",
            speaker="leader",
            content="谨遵神谕。",
        )
        engine.world.add_event(
            "god",
            "God granted 5 food to human",
            faction_id="human",
        )

        response = client.get("/api/debug/state")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["resources"], ["food", "wood", "stone"])
        self.assertEqual(payload["weather_types"], ["clear", "rain", "drought", "storm"])

        tile = payload["tiles"][0]
        self.assertEqual(
            set(tile),
            {
                "x",
                "y",
                "terrain",
                "owner",
                "home_of",
                "weather",
                "weather_duration",
                "population",
                "soldiers",
                "professions",
                "houses",
                "capacity",
                "protected",
            },
        )

        faction = next(
            item for item in payload["factions"] if item["faction_id"] == "human"
        )
        self.assertTrue(
            {
                "faction_id",
                "name",
                "leader_name",
                "resources",
                "population",
                "soldiers",
                "jobs",
                "houses",
                "population_capacity",
                "territory_count",
                "home_tile",
                "eliminated",
                "known_factions",
                "diplomacy",
                "last_plan_snapshot",
                "leader_memory",
                "leader_context_window_count",
            }.issubset(faction)
        )

        petition = payload["petitions"][0]
        self.assertEqual(
            set(petition),
            {
                "petition_id",
                "faction_id",
                "kind",
                "request",
                "reason",
                "urgency",
                "status",
                "created_tick",
            },
        )
        self.assertEqual(petition["kind"], "weather")

        chat = payload["god_chats"][-1]
        self.assertEqual(
            set(chat),
            {"message_id", "tick", "faction_id", "speaker", "content"},
        )
        self.assertEqual(chat["speaker"], "leader")

        event = payload["events"][-1]
        self.assertEqual(
            set(event),
            {"tick", "kind", "message", "faction_id"},
        )
        self.assertEqual(event["kind"], "god")

    def test_local_state_does_not_leak_far_world_information(self) -> None:
        client = self.make_client()

        local = client.get("/api/state").json()
        debug = client.get("/api/debug/state").json()

        self.assertLess(len(local["tiles"]), len(debug["tiles"]))
        local_coords = {(tile["x"], tile["y"]) for tile in local["tiles"]}
        visible = {
            (x, y)
            for y in range(
                local["player"]["y"] - local["player"]["vision_radius"],
                local["player"]["y"] + local["player"]["vision_radius"] + 1,
            )
            for x in range(
                local["player"]["x"] - local["player"]["vision_radius"],
                local["player"]["x"] + local["player"]["vision_radius"] + 1,
            )
            if abs(local["player"]["x"] - x) + abs(local["player"]["y"] - y)
            <= local["player"]["vision_radius"]
        }
        self.assertTrue(local_coords.issubset(visible))

    def test_god_mutation_endpoints_return_updated_state(self) -> None:
        client = self.make_client()

        give = client.post(
            "/api/god/give",
            json={"faction_id": "human", "resource": "food", "amount": 7},
        )
        weather = client.post(
            "/api/god/weather",
            json={"x": 0, "y": 0, "weather": "storm", "duration": 4},
        )

        self.assertEqual(give.status_code, 200)
        self.assertEqual(weather.status_code, 200)
        world = client.app.state.engine.world
        self.assertEqual(world.factions["human"].resources.food, 127)
        self.assertEqual(world.tile_at(0, 0).weather, "storm")
        self.assertEqual(world.tile_at(0, 0).weather_duration, 4)

    def test_god_claim_endpoint_is_removed(self) -> None:
        client = self.make_client()

        response = client.post(
            "/api/god/claim",
            json={"faction_id": "human", "x": 0, "y": 0},
        )

        self.assertEqual(response.status_code, 404)

    def test_answer_petition_endpoint_updates_inbox(self) -> None:
        client = self.make_client()
        engine = client.app.state.engine
        engine.world.add_petition(
            faction_id="human",
            kind="resources",
            request={"resource": "wood", "amount": 5},
            reason="build homes",
        )

        response = client.post(
            "/api/god/answer",
            json={"petition_id": 1, "approve": True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["petitions"], [])
        self.assertEqual(engine.world.petitions[0].status, "approved")

    def test_god_chat_endpoint_records_private_reply_without_mutating_world(self) -> None:
        client = self.make_client()
        engine = client.app.state.engine
        before_resources = engine.world.factions["human"].resources.as_dict()
        before_population = engine.world.total_population("human")
        before_soldiers = engine.world.total_soldiers("human")
        before_territory = len(engine.world.faction_tiles("human"))

        response = client.post(
            "/api/god/chat",
            json={
                "faction_id": "human",
                "message": "若你攻打兽人，我会赐予粮食。",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        chats = [
            message
            for message in payload["god_chats"]
            if message["faction_id"] == "human"
        ]
        self.assertEqual([message["speaker"] for message in chats], ["god", "leader"])
        self.assertEqual(chats[0]["content"], "若你攻打兽人，我会赐予粮食。")
        self.assertIn("谨遵神谕", chats[1]["content"])
        self.assertEqual(engine.leaders["human"].chat_calls, 1)
        self.assertEqual(engine.world.factions["human"].resources.as_dict(), before_resources)
        self.assertEqual(engine.world.total_population("human"), before_population)
        self.assertEqual(engine.world.total_soldiers("human"), before_soldiers)
        self.assertEqual(len(engine.world.faction_tiles("human")), before_territory)
        self.assertEqual(engine.world.factions["human"].active_orders, {})

    def test_player_move_changes_position_and_reveals_tiles(self) -> None:
        client = self.make_client()
        world = client.app.state.engine.world
        direction = _first_passable_direction(world)
        before = world.player.as_dict()

        response = client.post("/api/player/move", json={"direction": direction})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotEqual(
            (payload["player"]["x"], payload["player"]["y"]),
            (before["x"], before["y"]),
        )
        self.assertLess(payload["player"]["divine_power"], before["divine_power"])
        self.assertGreaterEqual(
            payload["player"]["discovered_tiles_count"],
            before["discovered_tiles_count"],
        )

    def test_player_move_rejects_impassable_tile(self) -> None:
        client = self.make_client()
        world = client.app.state.engine.world
        direction, target = _first_neighbor(world)
        world.tile_at(*target).terrain = "water"

        response = client.post("/api/player/move", json={"direction": direction})

        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot move into water or mountain", response.json()["error"])

    def test_player_help_spends_inventory_and_updates_faction(self) -> None:
        client = self.make_client()
        world = client.app.state.engine.world
        _put_player_on_faction_land(world, "human")
        before_inventory = world.player.inventory.food
        before_faction_food = world.factions["human"].resources.food

        response = client.post(
            "/api/player/help",
            json={
                "kind": "resources",
                "faction_id": "human",
                "resource": "food",
                "amount": 10,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(world.player.inventory.food, before_inventory - 10)
        self.assertEqual(world.factions["human"].resources.food, before_faction_food + 10)
        self.assertTrue(any(event.kind == "player_help" for event in world.events))

    def test_player_trade_spends_offer_and_advances_godhood(self) -> None:
        client = self.make_client()
        world = client.app.state.engine.world
        _put_player_on_faction_land(world, "human")
        before_inventory = world.player.inventory.food
        before_progress = world.player.godhood_progress

        response = client.post(
            "/api/player/trade",
            json={
                "faction_id": "human",
                "risk_level": "low",
                "offer": {"resource": "food", "amount": 5},
                "request": {"kind": "faith"},
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["last_interaction"]["success"])
        self.assertEqual(world.player.inventory.food, before_inventory - 5)
        self.assertGreater(world.player.godhood_progress, before_progress)
        self.assertTrue(any(event.kind == "player_trade" for event in world.events))

    def test_god_chat_endpoint_rejects_invalid_requests(self) -> None:
        client = self.make_client()

        empty = client.post(
            "/api/god/chat",
            json={"faction_id": "human", "message": "   "},
        )
        missing = client.post(
            "/api/god/chat",
            json={"faction_id": "missing", "message": "回应我。"},
        )

        self.assertEqual(empty.status_code, 400)
        self.assertIn("message must not be empty", empty.json()["error"])
        self.assertEqual(missing.status_code, 400)
        self.assertIn("unknown faction", missing.json()["error"])

    def test_god_chat_endpoint_rejects_eliminated_faction(self) -> None:
        client = self.make_client()
        client.app.state.engine.world.factions["human"].eliminated = True

        response = client.post(
            "/api/god/chat",
            json={"faction_id": "human", "message": "回应我。"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("is eliminated", response.json()["error"])

    def test_god_chat_endpoint_rejects_concurrent_request(self) -> None:
        client = self.make_client()
        asyncio.run(client.app.state.tick_lock.acquire())
        try:
            response = client.post(
                "/api/god/chat",
                json={"faction_id": "human", "message": "回应我。"},
            )
        finally:
            client.app.state.tick_lock.release()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "world is already advancing")

    def test_tick_endpoint_advances_world_and_fake_leaders(self) -> None:
        client = self.make_client(strategy_interval=1)

        response = client.post("/api/tick", json={"count": 1})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["tick"], 1)
        self.assertFalse(payload["paused"])
        for leader in client.app.state.engine.leaders.values():
            self.assertEqual(leader.calls, 1)

    def test_strategy_tick_response_includes_last_plan_snapshot_for_live_clients(self) -> None:
        client = self.make_client(strategy_interval=1)

        response = client.post("/api/tick", json={"count": 1})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        human = next(
            faction
            for faction in payload["factions"]
            if faction["faction_id"] == "human"
        )
        self.assertEqual(human["last_plan_snapshot"]["tick"], 1)
        self.assertEqual(
            human["last_plan_snapshot"]["strategy_summary"],
            "hold position",
        )
        self.assertEqual(
            human["last_plan_snapshot"]["orders"]["turn_intent"],
            "hold position",
        )

    def test_tick_endpoint_rejects_concurrent_request(self) -> None:
        client = self.make_client()
        asyncio.run(client.app.state.tick_lock.acquire())
        try:
            response = client.post("/api/tick", json={"count": 1})
        finally:
            client.app.state.tick_lock.release()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "world is already advancing")

    def test_mutation_errors_are_json(self) -> None:
        client = self.make_client()

        response = client.post(
            "/api/god/give",
            json={"faction_id": "missing", "resource": "food", "amount": 1},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unknown faction", response.json()["error"])


def _first_neighbor(world):
    directions = {
        "north": (0, -1),
        "south": (0, 1),
        "west": (-1, 0),
        "east": (1, 0),
    }
    for direction, (dx, dy) in directions.items():
        target = (world.player.x + dx, world.player.y + dy)
        if world.in_bounds(*target):
            return direction, target
    raise AssertionError("player has no in-bounds neighbor")


def _first_passable_direction(world) -> str:
    directions = {
        "north": (0, -1),
        "south": (0, 1),
        "west": (-1, 0),
        "east": (1, 0),
    }
    for direction, (dx, dy) in directions.items():
        target = (world.player.x + dx, world.player.y + dy)
        if world.in_bounds(*target) and world.tile_at(*target).is_passable():
            return direction
    raise AssertionError("player has no passable neighbor")


def _put_player_on_faction_land(world, faction_id: str) -> None:
    tile = world.faction_tiles(faction_id)[0]
    world.player.x = tile.x
    world.player.y = tile.y
    world.reveal_player_area()
    world.update_player_contacts()


if __name__ == "__main__":
    unittest.main()
