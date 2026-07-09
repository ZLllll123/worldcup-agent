"""Clean historical World Cup match and tournament CSV files.

This file is designed to run directly from PyCharm without program arguments.
It uses only the Python standard library and never modifies the source CSVs.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "data" / "processed" / "history"

# Direct aliases, official naming updates and confirmed spelling errors.
# Historical predecessor states not listed here remain separate teams.
TEAM_ALIASES = {
    "United States": "USA",
    "Iran": "IR Iran",
    "South Korea": "Korea Republic",
    "Turkey": "Türkiye",
    "Ivory Coast": "Côte d'Ivoire",
    "Czech Republic": "Czechia",
    "West Germany": "Germany",
    "Zaire": "Congo DR",
    "Portagul": "Portugal",
    "Columbia": "Colombia",
}

MATCH_SOURCE_CANDIDATES = [
    BASE_DIR / "wcmatches.csv",
    BASE_DIR / "data" / "raw" / "history" / "wcmatches.csv",
    Path(r"D:\download\archive\wcmatches.csv"),
]
TOURNAMENT_SOURCE_CANDIDATES = [
    BASE_DIR / "worldcups.csv",
    BASE_DIR / "data" / "raw" / "history" / "worldcups.csv",
    Path(r"D:\download\archive\worldcups.csv"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean historical World Cup CSV data."
    )
    parser.add_argument("--matches", type=Path, help="Path to wcmatches.csv")
    parser.add_argument("--cups", type=Path, help="Path to worldcups.csv")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    return parser.parse_args()


def find_source(explicit: Path | None, candidates: list[Path], filename: str) -> Path:
    if explicit:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Source file not found: {path}")
        return path

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    searched = "\n".join(f"- {path}" for path in candidates)
    raise FileNotFoundError(
        f"Could not find {filename}. Searched:\n{searched}\n"
        f"Copy {filename} next to this script or into data/raw/history/."
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def clean_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\u00a0", " ")).strip()


def parse_integer(value: str, field: str, row_number: int) -> int:
    cleaned = clean_whitespace(value).replace(",", "")
    try:
        return int(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"Invalid integer in {field} at source row {row_number}: {value!r}"
        ) from exc


class TeamNormalizer:
    def __init__(self) -> None:
        self.mapping_counts: Counter[tuple[str, str]] = Counter()

    def normalize(self, value: str) -> str:
        raw = clean_whitespace(value)
        if not raw or raw == "NA":
            return ""
        normalized = TEAM_ALIASES.get(raw, raw)
        self.mapping_counts[(raw, normalized)] += 1
        return normalized


def decision_method(win_conditions: str) -> str:
    lowered = win_conditions.lower()
    if "penalt" in lowered:
        return "penalties"
    if "aet" in lowered or "extra time" in lowered:
        return "extra_time"
    return "regular"


def winner_from_conditions(
    win_conditions: str,
    normalizer: TeamNormalizer,
) -> str:
    match = re.match(r"^(.+?)\s+won\b", win_conditions, flags=re.IGNORECASE)
    return normalizer.normalize(match.group(1)) if match else ""


def clean_matches(
    rows: list[dict[str, str]],
    normalizer: TeamNormalizer,
) -> tuple[list[dict[str, Any]], list[str]]:
    cleaned: list[dict[str, Any]] = []
    warnings: list[str] = []

    for row_number, row in enumerate(rows, start=2):
        year = parse_integer(row["year"], "year", row_number)
        home_score = parse_integer(row["home_score"], "home_score", row_number)
        away_score = parse_integer(row["away_score"], "away_score", row_number)
        home_team_raw = clean_whitespace(row["home_team"])
        away_team_raw = clean_whitespace(row["away_team"])
        home_team = normalizer.normalize(home_team_raw)
        away_team = normalizer.normalize(away_team_raw)
        conditions = clean_whitespace(row.get("win_conditions", ""))
        method = decision_method(conditions)

        if home_score > away_score:
            score_result = "H"
            winner = home_team
        elif away_score > home_score:
            score_result = "A"
            winner = away_team
        else:
            score_result = "D"
            winner = ""

        if method == "penalties":
            condition_winner = winner_from_conditions(conditions, normalizer)
            raw_winner = normalizer.normalize(row.get("winning_team", ""))
            if condition_winner in {home_team, away_team}:
                winner = condition_winner
            elif raw_winner in {home_team, away_team}:
                winner = raw_winner
            else:
                warnings.append(
                    f"Could not resolve penalty winner at wcmatches.csv row {row_number}"
                )

        loser = ""
        if winner == home_team:
            match_result = "H"
            loser = away_team
        elif winner == away_team:
            match_result = "A"
            loser = home_team
        else:
            match_result = "D"

        raw_winner = normalizer.normalize(row.get("winning_team", ""))
        if raw_winner and raw_winner not in {home_team, away_team}:
            warnings.append(
                f"Ignored inconsistent winning_team={raw_winner!r} "
                f"at wcmatches.csv row {row_number}"
            )

        cleaned.append(
            {
                "year": year,
                "date": clean_whitespace(row["date"]),
                "host_country": clean_whitespace(row["country"]),
                "city": clean_whitespace(row["city"]),
                "stage": clean_whitespace(row["stage"]),
                "home_team": home_team,
                "away_team": away_team,
                "home_team_raw": home_team_raw,
                "away_team_raw": away_team_raw,
                "home_score": home_score,
                "away_score": away_score,
                "score_result": score_result,
                "match_result": match_result,
                "decision_method": method,
                "winner": winner,
                "loser": loser,
                "win_conditions_raw": conditions,
                "outcome_raw": clean_whitespace(row.get("outcome", "")),
                "source_row_number": row_number,
            }
        )

    return cleaned, warnings


def clean_tournaments(
    rows: list[dict[str, str]],
    normalizer: TeamNormalizer,
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=2):
        cleaned.append(
            {
                "year": parse_integer(row["year"], "year", row_number),
                "host": clean_whitespace(row["host"]),
                "winner": normalizer.normalize(row["winner"]),
                "runner_up": normalizer.normalize(row["second"]),
                "third": normalizer.normalize(row["third"]),
                "fourth": normalizer.normalize(row["fourth"]),
                "goals_scored": parse_integer(
                    row["goals_scored"], "goals_scored", row_number
                ),
                "teams": parse_integer(row["teams"], "teams", row_number),
                "games": parse_integer(row["games"], "games", row_number),
                "attendance": parse_integer(
                    row["attendance"], "attendance", row_number
                ),
                "source_row_number": row_number,
            }
        )
    return cleaned


def validate_cleaned_data(
    matches: list[dict[str, Any]],
    tournaments: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    if not matches:
        errors.append("No historical matches were produced")
    if not tournaments:
        errors.append("No tournament rows were produced")

    tournament_years = [row["year"] for row in tournaments]
    duplicate_years = [
        year for year, count in Counter(tournament_years).items() if count > 1
    ]
    if duplicate_years:
        errors.append(f"Duplicate tournament years: {sorted(duplicate_years)}")

    for row in matches:
        if not row["home_team"] or not row["away_team"]:
            errors.append(f"Missing team at source row {row['source_row_number']}")
        if row["home_team"] == row["away_team"]:
            errors.append(f"Identical teams at source row {row['source_row_number']}")
        if row["decision_method"] == "penalties" and not row["winner"]:
            errors.append(
                f"Penalty match lacks winner at source row {row['source_row_number']}"
            )
    return errors


def main() -> int:
    args = parse_args()
    matches_path = find_source(args.matches, MATCH_SOURCE_CANDIDATES, "wcmatches.csv")
    cups_path = find_source(args.cups, TOURNAMENT_SOURCE_CANDIDATES, "worldcups.csv")
    output_dir = args.output_dir.expanduser().resolve()

    print(f"Using matches: {matches_path}")
    print(f"Using tournaments: {cups_path}")
    print(f"Output directory: {output_dir}")

    raw_matches = read_csv(matches_path)
    raw_tournaments = read_csv(cups_path)
    normalizer = TeamNormalizer()
    matches, warnings = clean_matches(raw_matches, normalizer)
    tournaments = clean_tournaments(raw_tournaments, normalizer)
    errors = validate_cleaned_data(matches, tournaments)
    if errors:
        print("CLEANING FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    match_fields = [
        "year",
        "date",
        "host_country",
        "city",
        "stage",
        "home_team",
        "away_team",
        "home_team_raw",
        "away_team_raw",
        "home_score",
        "away_score",
        "score_result",
        "match_result",
        "decision_method",
        "winner",
        "loser",
        "win_conditions_raw",
        "outcome_raw",
        "source_row_number",
    ]
    tournament_fields = [
        "year",
        "host",
        "winner",
        "runner_up",
        "third",
        "fourth",
        "goals_scored",
        "teams",
        "games",
        "attendance",
        "source_row_number",
    ]
    mapping_rows = [
        {
            "raw_team": raw,
            "normalized_team": normalized,
            "occurrences": count,
            "changed": raw != normalized,
        }
        for (raw, normalized), count in sorted(normalizer.mapping_counts.items())
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "historical_matches_clean.csv", matches, match_fields)
    write_csv(output_dir / "tournament_results_clean.csv", tournaments, tournament_fields)
    write_csv(
        output_dir / "team_name_mapping.csv",
        mapping_rows,
        ["raw_team", "normalized_team", "occurrences", "changed"],
    )

    years = sorted({row["year"] for row in tournaments})
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "matches": str(matches_path),
            "tournaments": str(cups_path),
        },
        "output_directory": str(output_dir),
        "historical_match_rows": len(matches),
        "tournament_rows": len(tournaments),
        "first_tournament_year": years[0],
        "last_tournament_year": years[-1],
        "decision_methods": dict(
            sorted(Counter(row["decision_method"] for row in matches).items())
        ),
        "changed_team_names": sum(
            count
            for (raw, normalized), count in normalizer.mapping_counts.items()
            if raw != normalized
        ),
        "warnings": warnings,
        "known_data_gaps": (
            ["2022 World Cup is not present in the supplied files"]
            if 2022 not in years
            else []
        ),
        "legacy_teams_kept_separate": [
            "Czechoslovakia",
            "East Germany",
            "Soviet Union",
            "Yugoslavia",
        ],
    }
    (output_dir / "cleaning_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("CLEANING PASSED")
    print(f"- historical matches: {len(matches)}")
    print(f"- tournaments: {len(tournaments)}")
    print(f"- warnings: {len(warnings)}")
    if report["known_data_gaps"]:
        print(f"- known gap: {report['known_data_gaps'][0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
