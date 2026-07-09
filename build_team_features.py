"""Build explainable 2026 World Cup team features.

Run directly from PyCharm after the FIFA and historical cleaning scripts.
Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


BASE_DIR = Path(__file__).resolve().parent
FIFA_PROCESSED_ROOT = BASE_DIR / "data" / "processed" / "fifa"
HISTORY_PROCESSED_DIR = BASE_DIR / "data" / "processed" / "history"
MODEL_ROOT = BASE_DIR / "data" / "model"

TARGET_YEAR = 2026
HISTORY_HALF_LIFE_YEARS = 12.0
CURRENT_MATCH_WEIGHT = 4.0
RATE_PRIOR_MATCHES = 6.0
INITIAL_ELO = 1500.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build World Cup team features.")
    parser.add_argument("--fifa-dir", type=Path, help="Processed FIFA snapshot")
    parser.add_argument("--history-dir", type=Path, help="Processed history directory")
    parser.add_argument("--output-dir", type=Path, help="Feature output directory")
    return parser.parse_args()


def latest_fifa_snapshot(root: Path) -> Path:
    if not root.is_dir():
        raise FileNotFoundError(f"FIFA data directory not found: {root}")
    candidates = sorted(
        path for path in root.iterdir()
        if path.is_dir()
        and (path / "matches.csv").is_file()
        and (path / "rankings.csv").is_file()
    )
    if not candidates:
        raise FileNotFoundError(
            f"No processed FIFA snapshots in {root}. Run transform_fifa.py first."
        )
    return candidates[-1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def result_score(result: str) -> float:
    return {"W": 1.0, "D": 0.5, "L": 0.0}[result]


def update_elo(
    ratings: dict[str, float],
    team_a: str,
    team_b: str,
    score_a: float,
    k_factor: float,
) -> None:
    rating_a = ratings.get(team_a, INITIAL_ELO)
    rating_b = ratings.get(team_b, INITIAL_ELO)
    expected_a = 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))
    change = k_factor * (score_a - expected_a)
    ratings[team_a] = rating_a + change
    ratings[team_b] = rating_b - change


def history_result(row: dict[str, str]) -> tuple[str, str]:
    match_result = row["match_result"]
    if match_result == "H":
        return "W", "L"
    if match_result == "A":
        return "L", "W"
    return "D", "D"


def current_result(row: dict[str, str]) -> tuple[str, str]:
    score_a = int(row["score_a"])
    score_b = int(row["score_b"])
    if score_a > score_b:
        return "W", "L"
    if score_b > score_a:
        return "L", "W"
    if row["penalty_a"] and row["penalty_b"]:
        if int(row["penalty_a"]) > int(row["penalty_b"]):
            return "W", "L"
        return "L", "W"
    return "D", "D"


def add_stats(
    stats: dict[str, dict[str, float]],
    team: str,
    result: str,
    goals_for: int,
    goals_against: int,
    weight: float,
) -> None:
    item = stats[team]
    item["matches"] += 1.0
    item["weighted_matches"] += weight
    item["weighted_points"] += weight * (3.0 if result == "W" else 1.0 if result == "D" else 0.0)
    item["weighted_goals_for"] += weight * goals_for
    item["weighted_goals_against"] += weight * goals_against


def empty_stats() -> dict[str, float]:
    return {
        "matches": 0.0,
        "weighted_matches": 0.0,
        "weighted_points": 0.0,
        "weighted_goals_for": 0.0,
        "weighted_goals_against": 0.0,
    }


def main() -> int:
    args = parse_args()
    fifa_dir = args.fifa_dir.resolve() if args.fifa_dir else latest_fifa_snapshot(FIFA_PROCESSED_ROOT)
    history_dir = args.history_dir.resolve() if args.history_dir else HISTORY_PROCESSED_DIR
    output_dir = args.output_dir.resolve() if args.output_dir else MODEL_ROOT / fifa_dir.name

    history_matches_path = history_dir / "historical_matches_clean.csv"
    tournaments_path = history_dir / "tournament_results_clean.csv"
    if not history_matches_path.is_file() or not tournaments_path.is_file():
        raise FileNotFoundError(
            f"Historical cleaned data missing in {history_dir}. "
            "Run clean_historical_data.py first."
        )

    historical = read_csv(history_matches_path)
    tournaments = read_csv(tournaments_path)
    current_matches = read_csv(fifa_dir / "matches.csv")
    rankings = read_csv(fifa_dir / "rankings.csv")

    tournament_teams = sorted(
        {row["team_a"] for row in current_matches if row["stage"] == "group_stage"}
        | {row["team_b"] for row in current_matches if row["stage"] == "group_stage"}
    )
    if len(tournament_teams) != 48:
        raise ValueError(f"Expected 48 tournament teams, found {len(tournament_teams)}")

    ranking_by_team = {row["team"]: row for row in rankings}
    missing_rankings = sorted(set(tournament_teams) - set(ranking_by_team))
    if missing_rankings:
        raise ValueError(f"Missing FIFA rankings for: {', '.join(missing_rankings)}")

    history_stats: dict[str, dict[str, float]] = defaultdict(empty_stats)
    current_stats: dict[str, dict[str, float]] = defaultdict(empty_stats)
    current_records: dict[str, Counter[str]] = defaultdict(Counter)
    ratings: dict[str, float] = {}

    historical_sorted = sorted(historical, key=lambda row: (row["date"], int(row["source_row_number"])))
    total_historical_goals = 0
    for row in historical_sorted:
        team_a, team_b = row["home_team"], row["away_team"]
        goals_a, goals_b = int(row["home_score"]), int(row["away_score"])
        result_a, result_b = history_result(row)
        age = TARGET_YEAR - int(row["year"])
        weight = 0.5 ** (age / HISTORY_HALF_LIFE_YEARS)
        add_stats(history_stats, team_a, result_a, goals_a, goals_b, weight)
        add_stats(history_stats, team_b, result_b, goals_b, goals_a, weight)
        total_historical_goals += goals_a + goals_b

        if row["decision_method"] == "penalties":
            elo_score_a = 0.75 if result_a == "W" else 0.25
        else:
            elo_score_a = result_score(result_a)
        k_factor = 28.0 if not row["stage"].lower().startswith("group") else 20.0
        update_elo(ratings, team_a, team_b, elo_score_a, k_factor)

    completed_current = sorted(
        (row for row in current_matches if row["status"] == "completed"),
        key=lambda row: (row["date"], row["match_id"]),
    )
    total_current_goals = 0
    for row in completed_current:
        team_a, team_b = row["team_a"], row["team_b"]
        goals_a, goals_b = int(row["score_a"]), int(row["score_b"])
        result_a, result_b = current_result(row)
        add_stats(current_stats, team_a, result_a, goals_a, goals_b, 1.0)
        add_stats(current_stats, team_b, result_b, goals_b, goals_a, 1.0)
        current_records[team_a][result_a] += 1
        current_records[team_b][result_b] += 1
        total_current_goals += goals_a + goals_b

        if row["decided_by"] == "penalties":
            elo_score_a = 0.75 if result_a == "W" else 0.25
        else:
            elo_score_a = result_score(result_a)
        update_elo(ratings, team_a, team_b, elo_score_a, 36.0)

    titles = Counter(row["winner"] for row in tournaments)
    runner_ups = Counter(row["runner_up"] for row in tournaments)
    global_goals_per_team_match = (
        (total_historical_goals + total_current_goals)
        / (2.0 * (len(historical) + len(completed_current)))
    )
    max_fifa_points = max(float(row["points"]) for row in rankings)

    features: list[dict[str, Any]] = []
    for team in tournament_teams:
        hist = history_stats[team]
        curr = current_stats[team]
        combined_exposure = hist["weighted_matches"] + CURRENT_MATCH_WEIGHT * curr["matches"]
        goals_for = hist["weighted_goals_for"] + CURRENT_MATCH_WEIGHT * curr["weighted_goals_for"]
        goals_against = hist["weighted_goals_against"] + CURRENT_MATCH_WEIGHT * curr["weighted_goals_against"]
        smoothed_for = (
            goals_for + RATE_PRIOR_MATCHES * global_goals_per_team_match
        ) / (combined_exposure + RATE_PRIOR_MATCHES)
        smoothed_against = (
            goals_against + RATE_PRIOR_MATCHES * global_goals_per_team_match
        ) / (combined_exposure + RATE_PRIOR_MATCHES)

        historical_ppg = (
            hist["weighted_points"] / hist["weighted_matches"]
            if hist["weighted_matches"] else 0.0
        )
        current_ppg = (
            curr["weighted_points"] / curr["matches"] if curr["matches"] else 0.0
        )
        elo = ratings.get(team, INITIAL_ELO)
        elo_index = 1.0 / (1.0 + 10.0 ** ((INITIAL_ELO - elo) / 400.0))
        fifa_points = float(ranking_by_team[team]["points"])
        fifa_index = fifa_points / max_fifa_points
        form_index = current_ppg / 3.0 if curr["matches"] else historical_ppg / 3.0
        strength_index = 0.50 * fifa_index + 0.30 * elo_index + 0.20 * form_index

        features.append(
            {
                "team": team,
                "fifa_rank": int(ranking_by_team[team]["rank"]),
                "fifa_points": round(fifa_points, 2),
                "elo_rating": round(elo, 2),
                "historical_matches": int(hist["matches"]),
                "weighted_historical_matches": round(hist["weighted_matches"], 4),
                "historical_ppg": round(historical_ppg, 4),
                "historical_goals_for_pg": round(
                    hist["weighted_goals_for"] / hist["weighted_matches"], 4
                ) if hist["weighted_matches"] else "",
                "historical_goals_against_pg": round(
                    hist["weighted_goals_against"] / hist["weighted_matches"], 4
                ) if hist["weighted_matches"] else "",
                "world_cup_titles": titles[team],
                "runner_up_finishes": runner_ups[team],
                "current_matches": int(curr["matches"]),
                "current_wins": current_records[team]["W"],
                "current_draws": current_records[team]["D"],
                "current_losses": current_records[team]["L"],
                "current_ppg": round(current_ppg, 4),
                "current_goals_for_pg": round(
                    curr["weighted_goals_for"] / curr["matches"], 4
                ) if curr["matches"] else "",
                "current_goals_against_pg": round(
                    curr["weighted_goals_against"] / curr["matches"], 4
                ) if curr["matches"] else "",
                "attack_index": round(smoothed_for / global_goals_per_team_match, 4),
                "defense_conceding_index": round(
                    smoothed_against / global_goals_per_team_match, 4
                ),
                "strength_index": round(strength_index, 6),
            }
        )

    features.sort(key=lambda row: (-float(row["strength_index"]), row["team"]))
    fields = list(features[0].keys())
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "team_features.csv", features, fields)

    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "fifa_snapshot_id": fifa_dir.name,
        "fifa_snapshot_directory": str(fifa_dir),
        "history_directory": str(history_dir),
        "team_count": len(features),
        "completed_2026_matches": len(completed_current),
        "global_goals_per_team_match": global_goals_per_team_match,
        "parameters": {
            "history_half_life_years": HISTORY_HALF_LIFE_YEARS,
            "current_match_weight": CURRENT_MATCH_WEIGHT,
            "rate_prior_matches": RATE_PRIOR_MATCHES,
            "initial_elo": INITIAL_ELO,
            "strength_weights": {
                "fifa_points": 0.50,
                "elo": 0.30,
                "current_form": 0.20,
            },
        },
        "known_limitations": [
            "The supplied historical dataset ends in 2018 and omits World Cup 2022.",
            "Player injuries, expected goals and squad availability are not included.",
        ],
    }
    (output_dir / "feature_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("FEATURE BUILD PASSED")
    print(f"- FIFA snapshot: {fifa_dir.name}")
    print(f"- teams: {len(features)}")
    print(f"- completed 2026 matches: {len(completed_current)}")
    print(f"- output: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
