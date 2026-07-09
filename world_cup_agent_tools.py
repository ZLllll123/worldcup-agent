"""Provider-neutral Agent tools for the World Cup prediction system.

Import TOOL_SCHEMAS and execute_tool from an LLM function-calling loop, or run
this file directly in PyCharm for a local self-check.
"""

from __future__ import annotations

import csv
import json
import math
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from predict_world_cup import (
    fixture_prediction,
    resolve_team,
    simulate_fixture,
)


BASE_DIR = Path(__file__).resolve().parent
MODEL_ROOT = BASE_DIR / "data" / "model"
PREDICTION_ROOT = BASE_DIR / "data" / "predictions"
DEFAULT_SIMULATIONS = 10_000
DEFAULT_SEED = 20260708


class ToolError(ValueError):
    """A safe, user-facing tool execution error."""


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def percentage(value: float) -> str:
    return f"{value:.1%}"


class WorldCupAgentTools:
    def __init__(
        self,
        model_root: Path = MODEL_ROOT,
        prediction_root: Path = PREDICTION_ROOT,
    ) -> None:
        self.model_root = model_root.resolve()
        self.prediction_root = prediction_root.resolve()
        self.snapshot_id = self._latest_common_snapshot()
        self.model_dir = self.model_root / self.snapshot_id
        self.prediction_dir = self.prediction_root / self.snapshot_id

        self.feature_metadata = json.loads(
            (self.model_dir / "feature_metadata.json").read_text(encoding="utf-8")
        )
        self.prediction_report = json.loads(
            (self.prediction_dir / "prediction_report.json").read_text(encoding="utf-8")
        )
        self.fifa_dir = Path(self.feature_metadata["fifa_snapshot_directory"])
        self.matches = read_csv(self.fifa_dir / "matches.csv")
        self.feature_rows = read_csv(self.model_dir / "team_features.csv")
        self.features = self._numeric_features(self.feature_rows)
        self.probabilities = read_csv(
            self.prediction_dir / "tournament_probabilities.csv"
        )
        self.cached_bracket = json.loads(
            (self.prediction_dir / "bracket_prediction.json").read_text(
                encoding="utf-8"
            )
        )
        self.base_goals = float(
            self.feature_metadata["global_goals_per_team_match"]
        )

    def _latest_common_snapshot(self) -> str:
        if not self.model_root.is_dir():
            raise FileNotFoundError(
                f"Model directory not found: {self.model_root}. "
                "Run build_team_features.py first."
            )
        if not self.prediction_root.is_dir():
            raise FileNotFoundError(
                f"Prediction directory not found: {self.prediction_root}. "
                "Run predict_world_cup.py first."
            )
        model_ids = {
            path.name
            for path in self.model_root.iterdir()
            if path.is_dir()
            and (path / "team_features.csv").is_file()
            and (path / "feature_metadata.json").is_file()
        }
        prediction_ids = {
            path.name
            for path in self.prediction_root.iterdir()
            if path.is_dir()
            and (path / "tournament_probabilities.csv").is_file()
            and (path / "prediction_report.json").is_file()
        }
        common = sorted(model_ids & prediction_ids)
        if not common:
            raise FileNotFoundError(
                "No matching model/prediction snapshot. Run build_team_features.py "
                "and then predict_world_cup.py."
            )
        return common[-1]

    @staticmethod
    def _numeric_features(
        rows: list[dict[str, str]],
    ) -> dict[str, dict[str, float]]:
        numeric_fields = {
            "fifa_rank",
            "fifa_points",
            "elo_rating",
            "attack_index",
            "defense_conceding_index",
            "strength_index",
        }
        return {
            row["team"]: {
                field: float(row[field]) for field in numeric_fields
            }
            for row in rows
        }

    def _resolve_team(self, requested: str) -> str:
        requested = requested.strip()
        if requested in self.features:
            return requested
        matches = [team for team in self.features if team.casefold() == requested.casefold()]
        if len(matches) == 1:
            return matches[0]
        aliases = {
            "united states": "USA",
            "us": "USA",
            "iran": "IR Iran",
            "south korea": "Korea Republic",
            "turkey": "Türkiye",
            "ivory coast": "Côte d'Ivoire",
            "czech republic": "Czechia",
            "dr congo": "Congo DR",
        }
        alias = aliases.get(requested.casefold())
        if alias in self.features:
            return alias
        suggestions = [
            team for team in sorted(self.features)
            if requested.casefold() in team.casefold()
            or team.casefold() in requested.casefold()
        ][:5]
        suffix = f" Possible matches: {', '.join(suggestions)}" if suggestions else ""
        raise ToolError(f"Unknown 2026 team: {requested}.{suffix}")

    def health_check(self) -> dict[str, Any]:
        return {
            "ok": True,
            "snapshot_id": self.snapshot_id,
            "team_count": len(self.features),
            "match_count": len(self.matches),
            "cached_simulations": self.prediction_report["simulations"],
        }

    def get_schedule(
        self,
        status: str = "remaining",
        team: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if status not in {"remaining", "completed", "all"}:
            raise ToolError("status must be remaining, completed, or all")
        if not 1 <= limit <= 104:
            raise ToolError("limit must be between 1 and 104")
        resolved_team = self._resolve_team(team) if team else None
        rows = []
        for row in self.matches:
            if status == "remaining" and row["status"] != "scheduled":
                continue
            if status == "completed" and row["status"] != "completed":
                continue
            if resolved_team and resolved_team not in {row["team_a"], row["team_b"]}:
                continue
            rows.append(
                {
                    "match_id": row["match_id"],
                    "date": row["date"],
                    "stage": row["stage"],
                    "status": row["status"],
                    "team_a": row["team_a"],
                    "team_b": row["team_b"],
                    "score": (
                        f"{row['score_a']}-{row['score_b']}"
                        if row["status"] == "completed" else None
                    ),
                    "kickoff_time_et": row["kickoff_time_et"] or None,
                    "venue": row["venue"],
                }
            )
        rows.sort(
            key=lambda row: (
                row["date"],
                int(re.search(r"\d+", str(row["match_id"])).group()),
            )
        )
        return {
            "snapshot_id": self.snapshot_id,
            "status_filter": status,
            "team_filter": resolved_team,
            "total_found": len(rows),
            "matches": rows[:limit],
        }

    def get_team_profile(self, team: str) -> dict[str, Any]:
        resolved = self._resolve_team(team)
        feature = next(row for row in self.feature_rows if row["team"] == resolved)
        probability = next(
            (row for row in self.probabilities if row["team"] == resolved), None
        )
        team_matches = [
            row for row in self.matches
            if resolved in {row["team_a"], row["team_b"]}
            and row["status"] == "completed"
        ]
        team_matches.sort(key=lambda row: (row["date"], row["match_id"]), reverse=True)
        recent = []
        for row in team_matches[:5]:
            is_team_a = row["team_a"] == resolved
            goals_for = row["score_a"] if is_team_a else row["score_b"]
            goals_against = row["score_b"] if is_team_a else row["score_a"]
            recent.append(
                {
                    "date": row["date"],
                    "opponent": row["team_b"] if is_team_a else row["team_a"],
                    "score_from_team_perspective": f"{goals_for}-{goals_against}",
                    "stage": row["stage"],
                }
            )
        profile = {
            "team": resolved,
            "fifa_rank": int(feature["fifa_rank"]),
            "fifa_points": float(feature["fifa_points"]),
            "elo_rating": float(feature["elo_rating"]),
            "strength_index": float(feature["strength_index"]),
            "attack_index": float(feature["attack_index"]),
            "defense_conceding_index": float(feature["defense_conceding_index"]),
            "historical_matches": int(feature["historical_matches"]),
            "world_cup_titles": int(feature["world_cup_titles"]),
            "current_record": {
                "matches": int(feature["current_matches"]),
                "wins": int(feature["current_wins"]),
                "draws": int(feature["current_draws"]),
                "losses": int(feature["current_losses"]),
                "points_per_game": float(feature["current_ppg"]),
            },
            "recent_matches": recent,
        }
        if probability:
            profile["tournament_probabilities"] = {
                "semifinal": float(probability["semifinal_probability"]),
                "final": float(probability["final_probability"]),
                "champion": float(probability["champion_probability"]),
            }
        return profile

    def predict_match(self, team_a: str, team_b: str) -> dict[str, Any]:
        resolved_a = self._resolve_team(team_a)
        resolved_b = self._resolve_team(team_b)
        if resolved_a == resolved_b:
            raise ToolError("A team cannot play against itself")
        raw = fixture_prediction(
            resolved_a, resolved_b, self.features, self.base_goals
        )
        return {
            "snapshot_id": self.snapshot_id,
            "team_a": resolved_a,
            "team_b": resolved_b,
            "expected_goals": {
                resolved_a: round(raw["expected_goals_a"], 3),
                resolved_b: round(raw["expected_goals_b"], 3),
            },
            "probabilities_90_minutes": {
                f"{resolved_a}_win": round(raw["win_90_a"], 6),
                "draw": round(raw["draw_90"], 6),
                f"{resolved_b}_win": round(raw["win_90_b"], 6),
            },
            "advancement_probabilities": {
                resolved_a: round(raw["advance_a"], 6),
                resolved_b: round(raw["advance_b"], 6),
            },
            "most_likely_score": (
                f"{raw['most_likely_score_a']}-{raw['most_likely_score_b']}"
            ),
            "predicted_winner": resolved_a if raw["advance_a"] >= 0.5 else resolved_b,
            "top_scores": [
                {
                    "score": item["score"],
                    "probability": round(item["probability"], 6),
                }
                for item in raw["top_scores"]
            ],
        }

    def explain_prediction(self, team_a: str, team_b: str) -> dict[str, Any]:
        resolved_a = self._resolve_team(team_a)
        resolved_b = self._resolve_team(team_b)
        prediction = self.predict_match(resolved_a, resolved_b)
        profile_a = self.get_team_profile(resolved_a)
        profile_b = self.get_team_profile(resolved_b)
        evidence = []
        if profile_a["fifa_rank"] != profile_b["fifa_rank"]:
            better = resolved_a if profile_a["fifa_rank"] < profile_b["fifa_rank"] else resolved_b
            evidence.append(
                f"{better} has the better FIFA rank "
                f"({profile_a['fifa_rank']} vs {profile_b['fifa_rank']})."
            )
        if profile_a["elo_rating"] != profile_b["elo_rating"]:
            better = resolved_a if profile_a["elo_rating"] > profile_b["elo_rating"] else resolved_b
            evidence.append(f"{better} has the higher chronological World Cup Elo rating.")
        if profile_a["attack_index"] != profile_b["attack_index"]:
            better = resolved_a if profile_a["attack_index"] > profile_b["attack_index"] else resolved_b
            evidence.append(f"{better} has the stronger smoothed scoring index.")
        if profile_a["defense_conceding_index"] != profile_b["defense_conceding_index"]:
            better = (
                resolved_a
                if profile_a["defense_conceding_index"] < profile_b["defense_conceding_index"]
                else resolved_b
            )
            evidence.append(f"{better} has the lower expected conceding index.")
        winner = prediction["predicted_winner"]
        advancement = prediction["advancement_probabilities"][winner]
        return {
            "prediction": prediction,
            "summary": (
                f"The model favors {winner} to advance at {percentage(advancement)}; "
                f"the most likely 90-minute score is {prediction['most_likely_score']}."
            ),
            "evidence": evidence,
            "caveats": self.feature_metadata["known_limitations"],
        }

    def get_champion_probabilities(self, limit: int = 8) -> dict[str, Any]:
        if not 1 <= limit <= len(self.probabilities):
            raise ToolError(f"limit must be between 1 and {len(self.probabilities)}")
        rows = sorted(
            self.probabilities,
            key=lambda row: -float(row["champion_probability"]),
        )[:limit]
        return {
            "snapshot_id": self.snapshot_id,
            "simulations": self.prediction_report["simulations"],
            "teams": [
                {
                    "team": row["team"],
                    "semifinal_probability": float(row["semifinal_probability"]),
                    "final_probability": float(row["final_probability"]),
                    "champion_probability": float(row["champion_probability"]),
                }
                for row in rows
            ],
        }

    def get_bracket_prediction(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            **self.cached_bracket,
        }

    @staticmethod
    def _actual_winner(row: dict[str, str]) -> tuple[str, str] | None:
        if row["status"] != "completed":
            return None
        score_a, score_b = int(row["score_a"]), int(row["score_b"])
        if score_a > score_b:
            return row["team_a"], row["team_b"]
        if score_b > score_a:
            return row["team_b"], row["team_a"]
        if row["penalty_a"] and row["penalty_b"]:
            if int(row["penalty_a"]) > int(row["penalty_b"]):
                return row["team_a"], row["team_b"]
            return row["team_b"], row["team_a"]
        return None

    def simulate_tournament(
        self,
        simulations: int = DEFAULT_SIMULATIONS,
        seed: int = DEFAULT_SEED,
    ) -> dict[str, Any]:
        if not 100 <= simulations <= 100_000:
            raise ToolError("simulations must be between 100 and 100000")
        bracket = {
            int(row["fifa_match_number"]): row
            for row in self.matches
            if row["fifa_match_number"] and 97 <= int(row["fifa_match_number"]) <= 104
        }
        if set(bracket) != set(range(97, 105)):
            raise ToolError("Matches 97-104 are incomplete in the current snapshot")

        rng = random.Random(seed)
        semifinal_counts: Counter[str] = Counter()
        final_counts: Counter[str] = Counter()
        champion_counts: Counter[str] = Counter()
        original_quarterfinalists = sorted(
            {bracket[n]["team_a"] for n in range(97, 101)}
            | {bracket[n]["team_b"] for n in range(97, 101)}
        )
        for _ in range(simulations):
            winners: dict[int, str] = {}
            losers: dict[int, str] = {}
            for match_number in range(97, 105):
                row = bracket[match_number]
                actual = self._actual_winner(row)
                if actual:
                    winner, loser = actual
                else:
                    team_a = resolve_team(row["team_a"], winners, losers)
                    team_b = resolve_team(row["team_b"], winners, losers)
                    winner, loser = simulate_fixture(
                        team_a, team_b, self.features, self.base_goals, rng
                    )
                winners[match_number] = winner
                losers[match_number] = loser
                if 97 <= match_number <= 100:
                    semifinal_counts[winner] += 1
                elif 101 <= match_number <= 102:
                    final_counts[winner] += 1
                elif match_number == 104:
                    champion_counts[winner] += 1

        rows = [
            {
                "team": team,
                "semifinal_probability": semifinal_counts[team] / simulations,
                "final_probability": final_counts[team] / simulations,
                "champion_probability": champion_counts[team] / simulations,
            }
            for team in original_quarterfinalists
        ]
        rows.sort(key=lambda row: (-row["champion_probability"], row["team"]))
        champion_sum = sum(row["champion_probability"] for row in rows)
        if not math.isclose(champion_sum, 1.0, abs_tol=1e-9):
            raise AssertionError(f"Champion probability sum is {champion_sum}")
        return {
            "snapshot_id": self.snapshot_id,
            "simulations": simulations,
            "seed": seed,
            "teams": rows,
        }


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_schedule",
            "description": "Get completed or remaining 2026 World Cup matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["remaining", "completed", "all"]},
                    "team": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 104},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_team_profile",
            "description": "Get ranking, Elo, form, attack, defense and title history for a team.",
            "parameters": {
                "type": "object",
                "properties": {"team": {"type": "string"}},
                "required": ["team"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "predict_match",
            "description": "Predict score, 90-minute result and advancement probability for two 2026 teams.",
            "parameters": {
                "type": "object",
                "properties": {
                    "team_a": {"type": "string"},
                    "team_b": {"type": "string"},
                },
                "required": ["team_a", "team_b"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_prediction",
            "description": "Explain a match prediction using FIFA rank, Elo, attack and defense evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "team_a": {"type": "string"},
                    "team_b": {"type": "string"},
                },
                "required": ["team_a", "team_b"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_champion_probabilities",
            "description": "Get cached semifinal, final and champion probabilities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 8}
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bracket_prediction",
            "description": "Get the complete most-likely quarterfinal-to-final bracket.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_tournament",
            "description": "Run a fresh Monte Carlo simulation from the current bracket.",
            "parameters": {
                "type": "object",
                "properties": {
                    "simulations": {"type": "integer", "minimum": 100, "maximum": 100000},
                    "seed": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
    },
]


_DEFAULT_TOOLS: WorldCupAgentTools | None = None


def get_default_tools() -> WorldCupAgentTools:
    global _DEFAULT_TOOLS
    if _DEFAULT_TOOLS is None:
        _DEFAULT_TOOLS = WorldCupAgentTools()
    return _DEFAULT_TOOLS


def execute_tool(name: str, arguments: dict[str, Any] | str | None = None) -> dict[str, Any]:
    """Execute a registered tool and always return a JSON-serializable object."""
    if isinstance(arguments, str):
        try:
            parsed_arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"Invalid JSON arguments: {exc}"}
    else:
        parsed_arguments = arguments or {}

    tools = get_default_tools()
    registry: dict[str, Callable[..., dict[str, Any]]] = {
        "get_schedule": tools.get_schedule,
        "get_team_profile": tools.get_team_profile,
        "predict_match": tools.predict_match,
        "explain_prediction": tools.explain_prediction,
        "get_champion_probabilities": tools.get_champion_probabilities,
        "get_bracket_prediction": tools.get_bracket_prediction,
        "simulate_tournament": tools.simulate_tournament,
    }
    function = registry.get(name)
    if function is None:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    try:
        return {"ok": True, "result": function(**parsed_arguments)}
    except (ToolError, TypeError, ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}


def main() -> int:
    tools = get_default_tools()
    print(json.dumps(tools.health_check(), ensure_ascii=False, indent=2))
    print("\nRegistered tools:")
    for schema in TOOL_SCHEMAS:
        print(f"- {schema['function']['name']}")
    print("\nChampion probabilities:")
    print(
        json.dumps(
            execute_tool("get_champion_probabilities", {"limit": 8}),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
