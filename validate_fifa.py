"""Validate normalized FIFA rankings and World Cup match data."""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PROCESSED_DATA_DIR = BASE_DIR / "data" / "processed" / "fifa"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate processed FIFA CSV files.")
    parser.add_argument(
        "processed_dir",
        nargs="?",
        type=Path,
        help="Processed directory. If omitted, use the latest local result.",
    )
    parser.add_argument(
        "--minimum-rankings",
        type=int,
        default=48,
        help="Minimum acceptable ranking rows (default: 48).",
    )
    return parser.parse_args()


def find_latest_processed(processed_root: Path) -> Path:
    if not processed_root.is_dir():
        raise FileNotFoundError(
            f"Processed data directory not found: {processed_root}\n"
            "Run transform_fifa.py first."
        )
    candidates = sorted(
        path for path in processed_root.iterdir()
        if path.is_dir()
        and (path / "rankings.csv").is_file()
        and (path / "matches.csv").is_file()
    )
    if not candidates:
        raise FileNotFoundError(
            f"No processed FIFA data found in {processed_root}\n"
            "Run transform_fifa.py first."
        )
    return candidates[-1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def duplicate_values(rows: list[dict[str, str]], field: str) -> list[str]:
    counts = Counter(row[field] for row in rows if row[field])
    return sorted(value for value, count in counts.items() if count > 1)


def validate_rankings(
    rows: list[dict[str, str]], minimum_rankings: int
) -> list[str]:
    errors: list[str] = []
    if len(rows) < minimum_rankings:
        errors.append(
            f"rankings.csv has {len(rows)} rows; expected at least {minimum_rankings}. "
            "The 'Show full rankings' control may not have expanded."
        )

    duplicate_ranks = duplicate_values(rows, "rank")
    duplicate_teams = duplicate_values(rows, "team")
    if duplicate_ranks:
        errors.append(f"Duplicate ranking positions: {', '.join(duplicate_ranks[:10])}")
    if duplicate_teams:
        errors.append(f"Duplicate ranking teams: {', '.join(duplicate_teams[:10])}")

    for index, row in enumerate(rows, start=2):
        try:
            if int(row["rank"]) <= 0 or float(row["points"]) <= 0:
                raise ValueError
        except ValueError:
            errors.append(f"Invalid rank or points at rankings.csv line {index}")
        if not row["team"].strip():
            errors.append(f"Missing team at rankings.csv line {index}")
    return errors


def validate_matches(rows: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    if len(rows) != 104:
        errors.append(f"matches.csv has {len(rows)} rows; expected 104")

    stage_counts = Counter(row["stage"] for row in rows)
    expected_stage_counts = {
        "group_stage": 72,
        "round_of_32": 16,
        "round_of_16": 8,
        "quarter_final": 4,
        "semi_final": 2,
        "third_place": 1,
        "final": 1,
    }
    for stage, expected in expected_stage_counts.items():
        actual = stage_counts.get(stage, 0)
        if actual != expected:
            errors.append(f"Stage {stage} has {actual} rows; expected {expected}")

    knockout_numbers: list[int] = []
    for index, row in enumerate(rows, start=2):
        if not row["date"] or not row["team_a"] or not row["team_b"]:
            errors.append(f"Missing date or team at matches.csv line {index}")
        if row["fifa_match_number"]:
            knockout_numbers.append(int(row["fifa_match_number"]))
        if row["status"] == "completed":
            if row["score_a"] == "" or row["score_b"] == "":
                errors.append(f"Completed match lacks score at matches.csv line {index}")
        elif row["status"] == "scheduled":
            if row["score_a"] or row["score_b"]:
                errors.append(f"Scheduled match already has score at matches.csv line {index}")
        else:
            errors.append(f"Unknown match status at matches.csv line {index}")

        if row["decided_by"] == "penalties":
            if row["penalty_a"] == "" or row["penalty_b"] == "":
                errors.append(f"Penalty result is incomplete at matches.csv line {index}")

        current_number = int(row["fifa_match_number"]) if row["fifa_match_number"] else 0
        for team_field in ("team_a", "team_b"):
            reference = re.fullmatch(
                r"(?:Winner|Runner-up) match (\d+)", row[team_field]
            )
            if reference and int(reference.group(1)) >= current_number:
                errors.append(
                    f"Invalid bracket reference in match {current_number}: "
                    f"{row[team_field]}"
                )

    if sorted(knockout_numbers) != list(range(73, 105)):
        errors.append("Knockout match numbers must contain every number from 73 to 104")
    return errors


def main() -> int:
    args = parse_args()
    processed_dir = (
        args.processed_dir.resolve()
        if args.processed_dir
        else find_latest_processed(DEFAULT_PROCESSED_DATA_DIR)
    )
    print(f"Using processed data: {processed_dir}")
    rankings_path = processed_dir / "rankings.csv"
    matches_path = processed_dir / "matches.csv"
    if not rankings_path.is_file() or not matches_path.is_file():
        raise FileNotFoundError(
            f"rankings.csv and matches.csv are required in {processed_dir}"
        )

    rankings = read_csv(rankings_path)
    matches = read_csv(matches_path)
    errors = validate_rankings(rankings, args.minimum_rankings)
    errors.extend(validate_matches(matches))

    if errors:
        print("VALIDATION FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    print("VALIDATION PASSED")
    print(f"- ranking rows: {len(rankings)}")
    print(f"- match rows: {len(matches)}")
    print("- knockout match numbers: 73-104 complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
