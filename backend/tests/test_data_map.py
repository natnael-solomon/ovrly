import re
from pathlib import Path

from services import models
from services.database import metadata
from services.jobs import models as job_models


def test_data_map_covers_every_current_table_and_column():
    assert job_models.jobs.metadata is models.principals.metadata is metadata
    path = Path(__file__).resolve().parents[2] / "docs/operations/data-map.md"
    documented = {}
    for line in path.read_text().splitlines():
        cells = line.split("|")
        if len(cells) < 4:
            continue
        name = cells[1].strip().strip("`")
        if name in metadata.tables:
            documented[name] = set(re.findall(r"`([^`]+)`", cells[2]))
    assert documented == {
        name: set(table.columns.keys()) for name, table in metadata.tables.items()
    }
