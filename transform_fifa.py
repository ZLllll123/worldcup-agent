"""Transform one raw FIFA snapshot into rankings.csv and matches.csv."""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RAW_DATA_DIR = BASE_DIR / "data" / "raw" / "fifa"
DEFAULT_PROCESSED_DATA_DIR = BASE_DIR / "data" / "processed" / "fifa"

MONTHS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}

DATE_RE = re.compile(
    r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), "
    r"(\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})$"
)
GROUP_MATCH_RE = re.compile(
    r"^(.+?)\s+(\d+)-(\d+)\s+(.+?)\s+[–-]\s+"
    r"Group ([A-L])\s+[–-]\s+(.+)$"
)
KNOCKOUT_LINE_RE = re.compile(r"^Match\s+(\d+)\s+[–-]\s+(.+)$")
FINISHED_KNOCKOUT_RE = re.compile(
    r"^(.+?)\s+(\d+)-(\d+)\s+(.+?)"
    r"(?:\s+\((AET|PSO)(?:\s+(\d+)-(\d+))?\))?$"
)
SCHEDULED_KNOCKOUT_RE = re.compile(r"^(.+?)\s+v\s+(.+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a raw FIFA snapshot to normalized CSV files."
    )
    parser.add_argument(
        "snapshot_dir",
        nargs="?",
        type=Path,
        help="Raw snapshot directory. If omitted, use the latest local snapshot.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (default: data/processed/fifa/<snapshot id>).",
    )
    return parser.parse_args()


def find_latest_snapshot(raw_root: Path) -> Path:
    if not raw_root.is_dir():
        raise FileNotFoundError(
            f"Raw data directory not found: {raw_root}\n"
            "Run collect_fifa.py first."
        )
    candidates = sorted(
        path for path in raw_root.iterdir()
        if path.is_dir() and (path / "manifest.json").is_file()
    )
    if not candidates:
        raise FileNotFoundError(
            f"No FIFA snapshots found in {raw_root}\n"
            "Run collect_fifa.py first."
        )
    return candidates[-1]


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_source_metadata(snapshot_dir: Path, source_name: str) -> dict[str, Any]:
    return load_json(snapshot_dir / source_name / "metadata.json")


def parse_page_date(line: str) -> str | None:
    match = DATE_RE.fullmatch(line)
    if not match:
        return None
    day, month_name, year = match.groups()
    return date(int(year), MONTHS[month_name], int(day)).isoformat()


def stage_for_match_number(match_number: int) -> str:
    if 73 <= match_number <= 88:
        return "round_of_32"
    if 89 <= match_number <= 96:
        return "round_of_16"
    if 97 <= match_number <= 100:
        return "quarter_final"
    if 101 <= match_number <= 102:
        return "semi_final"
    if match_number == 103:
        return "third_place"
    if match_number == 104:
        return "final"
    raise ValueError(f"Unexpected knockout match number: {match_number}")


def parse_rankings(
    snapshot_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_name = "mens_world_ranking"
    source_dir = snapshot_dir / source_name
    metadata = read_source_metadata(snapshot_dir, source_name)
    records = load_json(source_dir / "dom_records.json")
    page_text = (source_dir / "page.txt").read_text(encoding="utf-8")

    update_match = re.search(
        r"Last official update:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
        page_text,
        flags=re.IGNORECASE,
    )
    official_update = update_match.group(1) if update_match else ""

    rankings: list[dict[str, Any]] = []
    for record in records:
        if record.get("tag") != "tr":
            continue
        cells = [normalize_text(cell) for cell in record.get("cells", []) if cell]
        if len(cells) < 2 or cells[0].lower().startswith("rank"):
            continue

        rank_match = re.match(r"^(\d+)(?:\s|$)", cells[0])
        if not rank_match:
            continue

        point_index = None
        points = None
        for index in range(len(cells) - 1, -1, -1):
            if re.fullmatch(r"\d{3,4}(?:\.\d+)?", cells[index]):
                point_index = index
                points = float(cells[index])
                break
        if point_index is None or point_index <= 1:
            continue

        team = cells[1]
        if not team or re.fullmatch(r"[+\-]?\d+(?:\.\d+)?", team):
            continue

        rankings.append(
            {
                "rank": int(rank_match.group(1)),
                "team": team,
                "points": points,
                "official_update": official_update,
                "collected_at_utc": metadata["collected_at_utc"],
                "source_url": metadata["final_url"],
                "raw_row": record["text"],
            }
        )

    unique_by_rank = {row["rank"]: row for row in rankings}
    return [unique_by_rank[key] for key in sorted(unique_by_rank)], metadata


def base_match_row(
    metadata: dict[str, Any],
    match_date: str,
    match_id: str,
) -> dict[str, Any]:
    return {
        "match_id": match_id,
        "fifa_match_number": "",
        "date": match_date,
        "stage": "",
        "group": "",
        "status": "",
        "team_a": "",
        "team_b": "",
        "score_a": "",
        "score_b": "",
        "penalty_a": "",
        "penalty_b": "",
        "decided_by": "",
        "kickoff_time_et": "",
        "venue": "",
        "collected_at_utc": metadata["collected_at_utc"],
        "source_url": metadata["final_url"],
        "raw_line": "",
    }


def parse_knockout_match(
    match_number: int,
    body: str,
    match_date: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    parts = [normalize_text(part) for part in re.split(r"\s+-\s+", body)]
    description = parts[0]
    row = base_match_row(metadata, match_date, str(match_number))
    row["fifa_match_number"] = match_number
    row["stage"] = stage_for_match_number(match_number)
    row["raw_line"] = f"Match {match_number} – {body}"

    finished = FINISHED_KNOCKOUT_RE.fullmatch(description)
    if finished:
        team_a, score_a, score_b, team_b, method, penalty_a, penalty_b = (
            finished.groups()
        )
        row.update(
            {
                "status": "completed",
                "team_a": team_a,
                "team_b": team_b,
                "score_a": int(score_a),
                "score_b": int(score_b),
                "penalty_a": int(penalty_a) if penalty_a else "",
                "penalty_b": int(penalty_b) if penalty_b else "",
                "decided_by": (
                    "penalties" if method == "PSO" else "extra_time"
                    if method == "AET"
                    else "regular"
                ),
                "venue": parts[1] if len(parts) >= 2 else "",
            }
        )
        return row

    scheduled = SCHEDULED_KNOCKOUT_RE.fullmatch(description)
    if scheduled:
        row.update(
            {
                "status": "scheduled",
                "team_a": scheduled.group(1),
                "team_b": scheduled.group(2),
                "kickoff_time_et": parts[1] if len(parts) >= 3 else "",
                "venue": parts[2] if len(parts) >= 3 else (
                    parts[1] if len(parts) >= 2 else ""
                ),
            }
        )
        return row

    raise ValueError(f"Cannot parse knockout line: Match {match_number} – {body}")


def parse_matches(snapshot_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_name = "world_cup_schedule_results"
    source_dir = snapshot_dir / source_name
    metadata = read_source_metadata(snapshot_dir, source_name)
    lines = (source_dir / "page.txt").read_text(encoding="utf-8").splitlines()

    matches: list[dict[str, Any]] = []
    current_date = ""
    group_sequence = 0

    for raw_line in lines:
        line = normalize_text(raw_line)
        if not line:
            continue

        parsed_date = parse_page_date(line)
        if parsed_date:
            current_date = parsed_date
            continue

        group_match = GROUP_MATCH_RE.fullmatch(line)
        if group_match:
            if not current_date:
                raise ValueError(f"Group match has no date context: {line}")
            team_a, score_a, score_b, team_b, group, venue = group_match.groups()
            group_sequence += 1
            row = base_match_row(metadata, current_date, f"G{group_sequence:03d}")
            row.update(
                {
                    "stage": "group_stage",
                    "group": group,
                    "status": "completed",
                    "team_a": team_a,
                    "team_b": team_b,
                    "score_a": int(score_a),
                    "score_b": int(score_b),
                    "decided_by": "regular",
                    "venue": venue,
                    "raw_line": line,
                }
            )
            matches.append(row)
            continue

        knockout_line = KNOCKOUT_LINE_RE.fullmatch(line)
        if knockout_line:
            if not current_date:
                raise ValueError(f"Knockout match has no date context: {line}")
            matches.append(
                parse_knockout_match(
                    match_number=int(knockout_line.group(1)),
                    body=knockout_line.group(2),
                    match_date=current_date,
                    metadata=metadata,
                )
            )

    return matches, metadata


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    snapshot_dir = (
        args.snapshot_dir.resolve()
        if args.snapshot_dir
        else find_latest_snapshot(DEFAULT_RAW_DATA_DIR)
    )
    if not (snapshot_dir / "manifest.json").is_file():
        raise FileNotFoundError(f"manifest.json not found in {snapshot_dir}")

    output_dir = (
        args.output_dir.resolve()
        if args.output_dir
        else (DEFAULT_PROCESSED_DATA_DIR / snapshot_dir.name).resolve()
    )
    print(f"Using raw snapshot: {snapshot_dir}")
    rankings, ranking_metadata = parse_rankings(snapshot_dir)
    matches, match_metadata = parse_matches(snapshot_dir)

    ranking_fields = [
        "rank",
        "team",
        "points",
        "official_update",
        "collected_at_utc",
        "source_url",
        "raw_row",
    ]
    match_fields = [
        "match_id",
        "fifa_match_number",
        "date",
        "stage",
        "group",
        "status",
        "team_a",
        "team_b",
        "score_a",
        "score_b",
        "penalty_a",
        "penalty_b",
        "decided_by",
        "kickoff_time_et",
        "venue",
        "collected_at_utc",
        "source_url",
        "raw_line",
    ]
    write_csv(output_dir / "rankings.csv", rankings, ranking_fields)
    write_csv(output_dir / "matches.csv", matches, match_fields)

    processed_manifest = {
        "snapshot_id": snapshot_dir.name,
        "source_snapshot_directory": str(snapshot_dir),
        "ranking_rows": len(rankings),
        "match_rows": len(matches),
        "ranking_source_sha256": ranking_metadata["text_sha256"],
        "match_source_sha256": match_metadata["text_sha256"],
    }
    (output_dir / "processed_manifest.json").write_text(
        json.dumps(processed_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {len(rankings)} rankings and {len(matches)} matches to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
