# CSE 412 Team 19 Phase 2

PostgreSQL database for the Two-Stage Cascade Mini-Fab dataset.

## Setup

Clone the repository and open its folder:

```bash
git clone https://github.com/YichenZeng221/cse412_Phase2.git
cd cse412_Phase2
```

Run all commands from this folder.

You need PostgreSQL 15 or later, `psql`, and `python3` (3.9 or later). No pip packages are needed. These commands use a macOS/Linux terminal. Tested on macOS with PostgreSQL 15.19.

Check your tools and connection:

```bash
psql --version
python3 --version
pg_isready
psql -X -d postgres -c 'SELECT current_user;'
```

If needed, set your connection before continuing. Both the SQL commands and Python verifier use these settings:

```bash
export PGHOST=localhost
export PGPORT=5432
export PGUSER=your_postgres_username
```

Put the course archive (`CSE_412_Semiconductor_Fabrication_Factory-<timestamp>-001.zip`, from the course materials) in `data/`. Any file name starting with `CSE_412_Semiconductor_Fabrication_Factory` works; to use a different path, set `TWOSTAGE_ARCHIVE=/path/to/archive.zip`.

Keep the ZIP unchanged. The importer reads it directly and checks a SHA-256 of the dataset CSVs inside it (the other files in the archive are ignored). It will stop with a checksum error if the data differs.

Create the database, tables, and data **once**:

```bash
psql -X -v ON_ERROR_STOP=1 -d postgres -f sql/00_create_database.sql
psql -X -v ON_ERROR_STOP=1 -d cse412_team19 -f sql/01_schema.sql
psql -X -v ON_ERROR_STOP=1 -d cse412_team19 -f sql/02_import.sql
```

Your PostgreSQL account needs permission to create a database. If the tables are already populated, go straight to testing. Repeating the schema or import step will stop with an error.

## Test

Check tables, row counts, keys, and constraints:

```bash
psql -X -v ON_ERROR_STOP=1 -d cse412_team19 -f sql/04_validate.sql
```

The last line should start with `PASS: all validation assertions completed`.

Compare every imported observation with the original CSV data:

```bash
python3 data/verify_data.py cse412_team19
```

Expected: `sha256_match: true`, 7,706 trajectories, and 3,235,437 source and database observations. This check reads the full dataset.

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

## Manual demo

Open the console:

```bash
psql -X -d cse412_team19
```

Run one statement at a time. Pause after each result.

### Show tables and data

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

### INSERT

Start a transaction:

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

### UPDATE

```sql
UPDATE public.experiment
SET tw_count = 3
WHERE experiment_id = 190000001 AND experiment_code = 'DEMO-TEAM19'
RETURNING experiment_id, experiment_code, pa_count, pb_count, tw_count;
```

Expected: `UPDATE 1`, with `tw_count = 3`.

### DELETE

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

### SELECT 1

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

### SELECT 2

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

## Sharing on GitHub

This public repository contains the code and Team 19's proposal and ER diagram. The original dataset and reports containing its results are excluded by `.gitignore`. Each teammate adds the course archive to `data/` as described in Setup; make sure `.gitignore` excludes it.

The dataset is provided for course/research use and has redistribution restrictions. See the [official dataset folder and data policy](https://sourceforge.net/projects/devs-suitesim/files/Dataset/MiniFab%20Cascade%20Two-Stage%20Factory/).

This data is provided by the Arizona Center for Integrative Modeling & Simulation for research and teaching purposes.
