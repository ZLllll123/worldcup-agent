"""Predict remaining World Cup matches and simulate the tournament bracket."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


BASE_DIR = Path(__file__).resolve().parent
MODEL_ROOT = BASE_DIR / "data" / "model"
PREDICTION_ROOT = BASE_DIR / "data" / "predictions"
SIMULATIONS = 50_000
RANDOM_SEED = 20260708
MAX_SCORE = 8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Predict the World Cup champion.")
    parser.add_argument("--model-dir", type=Path, help="Directory containing team_features.csv")
    parser.add_argument("--output-dir", type=Path, help="Prediction output directory")
    parser.add_argument("--simulations", type=int, default=SIMULATIONS)
    parser.add_argument("--seed", type=int, default=RANDOM_SEED)
    return parser.parse_args()


def latest_model_dir(root: Path) -> Path:
    if not root.is_dir():
        raise FileNotFoundError(f"Model directory not found: {root}")
    candidates = sorted(
        path for path in root.iterdir()
        if path.is_dir()
        and (path / "team_features.csv").is_file()
        and (path / "feature_metadata.json").is_file()
    )
    if not candidates:
        raise FileNotFoundError(
            f"No team features found in {root}. Run build_team_features.py first."
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


def poisson_probability(goals: int, expected: float) -> float:
    return math.exp(-expected) * expected**goals / math.factorial(goals)


def expected_goals(
    team_a: dict[str, float],
    team_b: dict[str, float],
    base_goals: float,
) -> tuple[float, float]:
    strength_gap = team_a["strength_index"] - team_b["strength_index"]
    lambda_a = (
        base_goals
        * team_a["attack_index"]
        * team_b["defense_conceding_index"]
        * math.exp(0.85 * strength_gap)
    )
    lambda_b = (
        base_goals
        * team_b["attack_index"]
        * team_a["defense_conceding_index"]
        * math.exp(-0.85 * strength_gap)
    )
    return min(max(lambda_a, 0.20), 3.50), min(max(lambda_b, 0.20), 3.50)


def tiebreak_probability(team_a: dict[str, float], team_b: dict[str, float]) -> float:
    gap = team_a["strength_index"] - team_b["strength_index"]
    probability = 1.0 / (1.0 + math.exp(-5.0 * gap))
    return min(max(probability, 0.20), 0.80)


def fixture_prediction(
    team_a_name: str,
    team_b_name: str,
    features: dict[str, dict[str, float]],
    base_goals: float,
) -> dict[str, Any]:
    team_a, team_b = features[team_a_name], features[team_b_name]
    lambda_a, lambda_b = expected_goals(team_a, team_b, base_goals)
    score_probabilities: list[tuple[float, int, int]] = []
    total = 0.0
    for score_a in range(MAX_SCORE + 1):
        for score_b in range(MAX_SCORE + 1):
            probability = (
                poisson_probability(score_a, lambda_a)
                * poisson_probability(score_b, lambda_b)
            )
            score_probabilities.append((probability, score_a, score_b))
            total += probability
    score_probabilities = [
        (probability / total, score_a, score_b)
        for probability, score_a, score_b in score_probabilities
    ]

    win_a = sum(p for p, a, b in score_probabilities if a > b)
    draw = sum(p for p, a, b in score_probabilities if a == b)
    win_b = sum(p for p, a, b in score_probabilities if a < b)
    tie_a = tiebreak_probability(team_a, team_b)
    advance_a = win_a + draw * tie_a
    most_likely = max(score_probabilities)
    top_scores = sorted(score_probabilities, reverse=True)[:5]
    return {
        "team_a": team_a_name,
        "team_b": team_b_name,
        "expected_goals_a": lambda_a,
        "expected_goals_b": lambda_b,
        "win_90_a": win_a,
        "draw_90": draw,
        "win_90_b": win_b,
        "advance_a": advance_a,
        "advance_b": 1.0 - advance_a,
        "most_likely_score_a": most_likely[1],
        "most_likely_score_b": most_likely[2],
        "top_scores": [
            {"score": f"{a}-{b}", "probability": p}
            for p, a, b in top_scores
        ],
    }


def poisson_sample(expected: float, rng: random.Random) -> int:
    threshold = math.exp(-expected)
    product = 1.0
    count = 0
    while product > threshold:
        count += 1
        product *= rng.random()
    return count - 1


def simulate_fixture(
    team_a_name: str,
    team_b_name: str,
    features: dict[str, dict[str, float]],
    base_goals: float,
    rng: random.Random,
) -> tuple[str, str]:
    team_a, team_b = features[team_a_name], features[team_b_name]
    lambda_a, lambda_b = expected_goals(team_a, team_b, base_goals)
    score_a = poisson_sample(lambda_a, rng)
    score_b = poisson_sample(lambda_b, rng)
    if score_a > score_b:
        return team_a_name, team_b_name
    if score_b > score_a:
        return team_b_name, team_a_name
    if rng.random() < tiebreak_probability(team_a, team_b):
        return team_a_name, team_b_name
    return team_b_name, team_a_name


def resolve_team(reference: str, winners: dict[int, str], losers: dict[int, str]) -> str:
    winner_match = re.fullmatch(r"Winner match (\d+)", reference)
    if winner_match:
        return winners[int(winner_match.group(1))]
    loser_match = re.fullmatch(r"Runner-up match (\d+)", reference)
    if loser_match:
        return losers[int(loser_match.group(1))]
    return reference


def load_features(path: Path) -> dict[str, dict[str, float]]:
    rows = read_csv(path)
    numeric_fields = {
        "fifa_rank",
        "fifa_points",
        "elo_rating",
        "attack_index",
        "defense_conceding_index",
        "strength_index",
    }
    features: dict[str, dict[str, float]] = {}
    for row in rows:
        features[row["team"]] = {
            field: float(row[field]) for field in numeric_fields
        }
    return features


def main() -> int:
    args = parse_args()
    if args.simulations <= 0:
        raise ValueError("--simulations must be positive")
    model_dir = args.model_dir.resolve() if args.model_dir else latest_model_dir(MODEL_ROOT)
    output_dir = args.output_dir.resolve() if args.output_dir else PREDICTION_ROOT / model_dir.name
    metadata = json.loads((model_dir / "feature_metadata.json").read_text(encoding="utf-8"))
    fifa_dir = Path(metadata["fifa_snapshot_directory"])
    matches = read_csv(fifa_dir / "matches.csv")
    features = load_features(model_dir / "team_features.csv")
    base_goals = float(metadata["global_goals_per_team_match"])

    bracket = {
        int(row["fifa_match_number"]): row
        for row in matches
        if row["status"] == "scheduled" and row["fifa_match_number"]
    }
    expected_numbers = set(range(97, 105))
    if set(bracket) != expected_numbers:
        raise ValueError(
            f"Expected scheduled matches 97-104, found {sorted(bracket)}"
        )

    rng = random.Random(args.seed)
    semifinal_counts: Counter[str] = Counter()
    final_counts: Counter[str] = Counter()
    champion_counts: Counter[str] = Counter()
    third_place_counts: Counter[str] = Counter()

    for _ in range(args.simulations):
        winners: dict[int, str] = {}
        losers: dict[int, str] = {}
        for match_number in range(97, 105):
            row = bracket[match_number]
            team_a = resolve_team(row["team_a"], winners, losers)
            team_b = resolve_team(row["team_b"], winners, losers)
            winner, loser = simulate_fixture(
                team_a, team_b, features, base_goals, rng
            )
            winners[match_number] = winner
            losers[match_number] = loser
            if 97 <= match_number <= 100:
                semifinal_counts[winner] += 1
            elif 101 <= match_number <= 102:
                final_counts[winner] += 1
            elif match_number == 103:
                third_place_counts[winner] += 1
            elif match_number == 104:
                champion_counts[winner] += 1

    quarterfinalists = sorted(
        {bracket[number]["team_a"] for number in range(97, 101)}
        | {bracket[number]["team_b"] for number in range(97, 101)}
    )
    probability_rows = []
    for team in quarterfinalists:
        probability_rows.append(
            {
                "team": team,
                "semifinal_probability": semifinal_counts[team] / args.simulations,
                "final_probability": final_counts[team] / args.simulations,
                "champion_probability": champion_counts[team] / args.simulations,
                "third_place_win_probability": third_place_counts[team] / args.simulations,
            }
        )
    probability_rows.sort(key=lambda row: (-row["champion_probability"], row["team"]))

    deterministic_winners: dict[int, str] = {}
    deterministic_losers: dict[int, str] = {}
    match_prediction_rows: list[dict[str, Any]] = []
    bracket_details: list[dict[str, Any]] = []
    for match_number in range(97, 105):
        row = bracket[match_number]
        team_a = resolve_team(row["team_a"], deterministic_winners, deterministic_losers)
        team_b = resolve_team(row["team_b"], deterministic_winners, deterministic_losers)
        prediction = fixture_prediction(team_a, team_b, features, base_goals)
        winner = team_a if prediction["advance_a"] >= 0.5 else team_b
        loser = team_b if winner == team_a else team_a
        deterministic_winners[match_number] = winner
        deterministic_losers[match_number] = loser
        match_prediction_rows.append(
            {
                "match_number": match_number,
                "date": row["date"],
                "stage": row["stage"],
                "team_a": team_a,
                "team_b": team_b,
                "expected_goals_a": round(prediction["expected_goals_a"], 3),
                "expected_goals_b": round(prediction["expected_goals_b"], 3),
                "win_90_a": round(prediction["win_90_a"], 6),
                "draw_90": round(prediction["draw_90"], 6),
                "win_90_b": round(prediction["win_90_b"], 6),
                "advance_a": round(prediction["advance_a"], 6),
                "advance_b": round(prediction["advance_b"], 6),
                "most_likely_score": (
                    f"{prediction['most_likely_score_a']}-"
                    f"{prediction['most_likely_score_b']}"
                ),
                "predicted_winner": winner,
            }
        )
        bracket_details.append(
            {
                "match_number": match_number,
                "team_a": team_a,
                "team_b": team_b,
                "predicted_winner": winner,
                "top_scores": prediction["top_scores"],
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(
        output_dir / "tournament_probabilities.csv",
        probability_rows,
        list(probability_rows[0].keys()),
    )
    write_csv(
        output_dir / "match_predictions.csv",
        match_prediction_rows,
        list(match_prediction_rows[0].keys()),
    )
    bracket_output = {
        "predicted_champion": deterministic_winners[104],
        "highest_simulated_champion_probability": probability_rows[0],
        "matches": bracket_details,
    }
    (output_dir / "bracket_prediction.json").write_text(
        json.dumps(bracket_output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_directory": str(model_dir),
        "fifa_snapshot_directory": str(fifa_dir),
        "simulations": args.simulations,
        "random_seed": args.seed,
        "base_goals_per_team_match": base_goals,
        "model": {
            "score_model": "independent Poisson goals",
            "advancement_model": "90-minute score plus strength-based tiebreak",
            "strength_inputs": "50% FIFA points, 30% Elo, 20% current form",
        },
        "limitations": metadata["known_limitations"],
    }
    (output_dir / "prediction_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    champion_sum = sum(row["champion_probability"] for row in probability_rows)
    if not math.isclose(champion_sum, 1.0, abs_tol=1e-9):
        raise AssertionError(f"Champion probabilities sum to {champion_sum}")

    print("PREDICTION PASSED")
    print(f"- simulations: {args.simulations}")
    print(f"- predicted champion: {deterministic_winners[104]}")
    print(
        f"- highest champion probability: {probability_rows[0]['team']} "
        f"({probability_rows[0]['champion_probability']:.2%})"
    )
    print(f"- output: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
