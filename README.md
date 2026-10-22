# CSE 412 Team 19 Phase 2

PostgreSQL database for the Two-Stage Cascade Mini-Fab dataset.

This project loads the Two-Stage Cascade Mini-Fab simulation dataset (18 experiments, 7,706 trajectories, 3,235,437 observations) from the unmodified course archive into eight PostgreSQL tables. The importer checks a SHA-256 of the dataset CSVs before reading anything, and separate scripts then confirm that the database matches the original CSV data cell for cell.

## Table of Contents

- [Background](#background)
- [Install](#install)
- [Usage](#usage)
- [Repository layout](#repository-layout)
- [Dataset and sharing](#dataset-and-sharing)
- [Maintainers](#maintainers)
- [Contributing](#contributing)
- [License](#license)

## Background

The data is the Two-Stage Cascade Mini-Fab dataset provided by the Arizona Center for Integrative Modeling & Simulation (ACIMS) for research and teaching. The schema follows the Entity-Relationship (ER) diagram from Team 19's Phase 1 work. See [Dataset and sharing](#dataset-and-sharing) for the data policy.

## Install

### Dependencies

- PostgreSQL 15 or later, with `psql`. Your PostgreSQL account needs permission to create a database.
- [uv](https://docs.astral.sh/uv/getting-started/installation/). `uv` creates the project environment (`.venv`) with the Python version pinned in `.python-version`. The scripts themselves use only the standard library.
- The course archive, `CSE_412_Semiconductor_Fabrication_Factory-<timestamp>-001.zip`, from the course materials.

These commands use a macOS/Linux terminal. Tested on macOS with PostgreSQL 15.19.

### Steps

Clone the repository and open its folder. Run all commands from this folder.

```bash
git clone https://github.com/YichenZeng221/cse412_Phase2.git
cd cse412_Phase2
```

Create and activate the Python environment. `uv sync` downloads the pinned Python if it is missing. Activate the environment in every new terminal before the steps below; the import runs `python3 data/prepare_data.py ...` through `psql`, so it must run in a terminal where the environment is active.

```bash
uv sync
source .venv/bin/activate
```

Check your tools and connection:

```bash
psql --version
uv --version
pg_isready
psql -X -d postgres -c 'SELECT current_user;'
```

If needed, set your connection first. Both the SQL commands and the Python verifier use these settings:

```bash
export PGHOST=localhost
export PGPORT=5432
export PGUSER=your_postgres_username
```

Put the course archive in `data/`. Any file name starting with `CSE_412_Semiconductor_Fabrication_Factory` works; to use a different path, set `TWOSTAGE_ARCHIVE=/path/to/archive.zip`. Keep the ZIP unchanged: the importer reads it directly and stops with a checksum error if the dataset CSVs inside differ (the other files in the archive are ignored).

Create the database, tables, and data **once**. These commands create `cse412_team19`, create its eight tables, and import the dataset:

```bash
psql -X -v ON_ERROR_STOP=1 -d postgres -f sql/00_create_database.sql
psql -X -v ON_ERROR_STOP=1 -d cse412_team19 -f sql/01_schema.sql
psql -X -v ON_ERROR_STOP=1 -d cse412_team19 -f sql/02_import.sql
```

If the tables are already populated, go straight to [Usage](#usage). Repeating the schema or import step stops with an error.

## Usage

### Validate

Check tables, row counts, keys, and constraints:

```bash
psql -X -v ON_ERROR_STOP=1 -d cse412_team19 -f sql/04_validate.sql
```

Expected: the last line starts with `PASS: all validation assertions completed`.

### Verify against the source data

Compare every imported observation with the original CSV data. This reads the full dataset, so it takes a while.

```bash
python3 data/verify_data.py cse412_team19
```

Expected: `sha256_match: true`, 7,706 trajectories, and 3,235,437 source and database observations.

| Table | Rows |
|---|---:|
| experiment | 18 |
| component | 28 |
| stage | 2 |
| machine | 12 |
| segment | 6 |
| transducer | 7 |
| trajectory | 7,706 |
| observation | 3,235,437 |

### Manual demo

Open the console:

```bash
psql -X -d cse412_team19
```

Run one statement at a time and pause after each result. To run the same demonstration as one script, use `psql -X -d cse412_team19 -f sql/03_demo.sql`.

#### Show tables and data

```text
\pset pager off
\dt public.*
\d public.trajectory
\d public.observation
```

```sql
SELECT * FROM public.experiment ORDER BY experiment_id;
SELECT * FROM public.component ORDER BY component_id;
SELECT * FROM public.stage ORDER BY stage_id;
SELECT * FROM public.machine ORDER BY component_id;
SELECT * FROM public.segment ORDER BY component_id;
SELECT * FROM public.transducer ORDER BY component_id;
SELECT * FROM public.trajectory ORDER BY trajectory_id LIMIT 5;
SELECT * FROM public.observation ORDER BY observation_id LIMIT 5;
```

#### INSERT

Start a transaction so the demo rows can be rolled back:

```sql
BEGIN;
```

```sql
INSERT INTO public.experiment
    (experiment_id, experiment_code, pa_count, pb_count, tw_count, simulation_steps)
VALUES (190000001, 'DEMO-TEAM19', 6, 6, 0, NULL)
RETURNING experiment_id, experiment_code, pa_count, pb_count, tw_count;
```

Expected: `INSERT 0 1`, with `tw_count = 0`.

#### UPDATE

```sql
UPDATE public.experiment
SET tw_count = 3
WHERE experiment_id = 190000001 AND experiment_code = 'DEMO-TEAM19'
RETURNING experiment_id, experiment_code, pa_count, pb_count, tw_count;
```

Expected: `UPDATE 1`, with `tw_count = 3`.

#### DELETE

```sql
DELETE FROM public.experiment
WHERE experiment_id = 190000001 AND experiment_code = 'DEMO-TEAM19'
RETURNING experiment_id, experiment_code, pa_count, pb_count, tw_count;
```

Expected: `DELETE 1`.

End the demo transaction:

```sql
ROLLBACK;
```

Check that the original 18 experiments remain and no demo row is left:

```sql
SELECT count(*) AS original_experiments,
       count(*) FILTER (WHERE experiment_code = 'DEMO-TEAM19') AS remaining_demo_rows
FROM public.experiment;
```

Expected: `18` and `0`.

#### SELECT 1

Show the first eight positive throughput states for experiment `6-6-0-508`:

```sql
SELECT e.experiment_code, c.component_name, t.variable_name,
       o.logical_time, round(o.value::NUMERIC, 6) AS throughput
FROM public.experiment e
JOIN public.trajectory t ON t.experiment_id = e.experiment_id
JOIN public.component c ON c.component_id = t.component_id
JOIN public.observation o ON o.trajectory_id = t.trajectory_id
WHERE e.experiment_code = '6-6-0-508'
  AND c.component_name = 'tr'
  AND t.variable_name = 'compute_Thru'
  AND t.trajectory_type = 'numeric'
  AND o.logical_time IS NOT NULL
  AND CASE WHEN t.trajectory_type = 'numeric'
           THEN o.value::NUMERIC > 0 ELSE false END
ORDER BY o.logical_time, o.observation_id
LIMIT 8;
```

Expected: 8 rows. The first throughput is `0.000449` at time `2218.877399171162`.

#### SELECT 2

Compare the last recorded throughput and average turnaround across all experiments:

```sql
WITH last_state AS (
    SELECT DISTINCT ON (e.experiment_id, t.variable_name)
           e.experiment_id, e.experiment_code, e.pa_count, e.pb_count, e.tw_count,
           t.variable_name, o.value::NUMERIC AS metric
    FROM public.experiment e
    JOIN public.trajectory t ON t.experiment_id = e.experiment_id
    JOIN public.component c ON c.component_id = t.component_id
    JOIN public.observation o ON o.trajectory_id = t.trajectory_id
    WHERE c.component_name = 'tr'
      AND t.variable_name IN ('compute_Thru', 'compute_TA')
      AND t.trajectory_type = 'numeric'
      AND o.logical_time IS NOT NULL
    ORDER BY e.experiment_id, t.variable_name,
             o.logical_time DESC, o.observation_id DESC
)
SELECT experiment_code, pa_count + pb_count + tw_count AS configured_lots,
       round(max(metric) FILTER (WHERE variable_name = 'compute_Thru'), 6) AS final_throughput,
       round(max(metric) FILTER (WHERE variable_name = 'compute_TA'), 3) AS final_avg_turnaround
FROM last_state
GROUP BY experiment_id, experiment_code, pa_count, pb_count, tw_count
ORDER BY experiment_id;
```

Expected: 18 rows. For `6-6-0-508`, throughput is `0.000526` and average turnaround is `2785.980`.

Exit with `\q`. If an input goes wrong, press **Control+C**, run `ROLLBACK;`, then start the INSERT/UPDATE/DELETE demo again from `BEGIN;`.

## Repository layout

| Path | Purpose |
|---|---|
| [`sql/00_create_database.sql`](sql/00_create_database.sql) | Creates the `cse412_team19` database if it is missing. |
| [`sql/01_schema.sql`](sql/01_schema.sql) | Creates the eight tables, constraints, and indexes. |
| [`sql/02_import.sql`](sql/02_import.sql) | Imports each table from `data/prepare_data.py` and advances the identity sequences past the imported IDs. |
| [`sql/03_demo.sql`](sql/03_demo.sql) | Scripted version of the manual demo. |
| [`sql/04_validate.sql`](sql/04_validate.sql) | Checks row counts, keys, constraints, and data invariants. |
| [`data/twostage.py`](data/twostage.py) | Shared reader for the course archive: checksum, ID assignment, and CSV parsing. |
| [`data/prepare_data.py`](data/prepare_data.py) | Exports each table as CSV for `\copy`; the `audit` argument prints a JSON audit of the archive. |
| [`data/verify_data.py`](data/verify_data.py) | Compares the database with the original CSV data. |
| [`pyproject.toml`](pyproject.toml), [`uv.lock`](uv.lock), [`.python-version`](.python-version) | `uv` project configuration. |

## Dataset and sharing

This repository contains the code and Team 19's proposal and ER diagram. The original dataset and reports containing its results are excluded by `.gitignore`. Each teammate adds the course archive to `data/` as described in [Install](#install). Before committing, confirm with `git status` that the archive does not appear as an untracked file.

The dataset is provided for course/research use and has redistribution restrictions. See the [official dataset folder and data policy](https://sourceforge.net/projects/devs-suitesim/files/Dataset/MiniFab%20Cascade%20Two-Stage%20Factory/).

This data is provided by the Arizona Center for Integrative Modeling & Simulation for research and teaching purposes.

## Maintainers

TODO: list the Team 19 maintainers, each with one way to contact them (a GitHub link or email).
