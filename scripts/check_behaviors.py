import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services.analysis import behavior_distribution
from backend.app.services.query import QueryFilters, connect, trajectory_source


def main() -> None:
    rows = behavior_distribution()
    assert [row["behavior"] for row in rows] == [
        "berthing", "anchoring", "turning", "straight", "speed_change"
    ]
    assert all(row["point_count"] >= 0 and row["vessel_count"] >= 0 for row in rows)
    assert any(row["point_count"] > 0 for row in rows)
    source, params = trajectory_source(QueryFilters(behavior="turning"))
    with connect() as con:
        assert con.execute(f"SELECT count(*) FROM {source}", params).fetchone()[0] > 0
    print(rows)


if __name__ == "__main__":
    main()
