\set ON_ERROR_STOP on
\pset pager off
\pset null '[NULL]'
\echo 'TEAM 19 - PostgreSQL console demonstration'
\conninfo
\dt public.*

\echo 'Imported rows in all eight tables'
SELECT 'experiment' AS table_name, count(*) AS rows FROM public.experiment
UNION ALL SELECT 'component', count(*) FROM public.component
UNION ALL SELECT 'stage', count(*) FROM public.stage
UNION ALL SELECT 'machine', count(*) FROM public.machine
UNION ALL SELECT 'segment', count(*) FROM public.segment
UNION ALL SELECT 'transducer', count(*) FROM public.transducer
UNION ALL SELECT 'trajectory', count(*) FROM public.trajectory
UNION ALL SELECT 'observation', count(*) FROM public.observation;

-- Preview five rows from the two large tables.
SELECT * FROM public.experiment ORDER BY experiment_id;
SELECT * FROM public.component ORDER BY component_id;
SELECT * FROM public.stage ORDER BY stage_id;
SELECT * FROM public.machine ORDER BY component_id;
SELECT * FROM public.segment ORDER BY component_id;
SELECT * FROM public.transducer ORDER BY component_id;
SELECT * FROM public.trajectory ORDER BY trajectory_id LIMIT 5;
SELECT * FROM public.observation ORDER BY observation_id LIMIT 5;

BEGIN;
-- Use a fixed demo ID to leave the identity sequence unchanged.
\echo '1. INSERT - add a temporary experiment configuration (1 point)'
INSERT INTO public.experiment
    (experiment_id, experiment_code, pa_count, pb_count, tw_count, simulation_steps)
VALUES (190000001, 'DEMO-TEAM19', 6, 6, 0, NULL)
RETURNING experiment_id, experiment_code, pa_count, pb_count, tw_count;

\echo '2. UPDATE - change the temporary test-wafer count (1 point)'
UPDATE public.experiment
SET tw_count = 3
WHERE experiment_id = 190000001 AND experiment_code = 'DEMO-TEAM19'
RETURNING experiment_id, experiment_code, pa_count, pb_count, tw_count;

\echo '3. DELETE - remove the temporary configuration (0.5 point)'
DELETE FROM public.experiment
WHERE experiment_id = 190000001 AND experiment_code = 'DEMO-TEAM19'
RETURNING experiment_id, experiment_code, pa_count, pb_count, tw_count;
ROLLBACK;

\echo '4. SELECT one - first positive factory throughput states in experiment 6-6-0-508 (0.75 point)'
-- observation_id keeps source order when event times are equal.
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

\echo '5. SELECT two - compare final factory performance across the 18 configurations (0.75 point)'
-- Use the last recorded throughput and average turnaround states.
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

\echo 'Demo cleanup check: 18 original configurations and zero demo rows'
SELECT count(*) AS original_experiments,
       count(*) FILTER (WHERE experiment_code = 'DEMO-TEAM19') AS remaining_demo_rows
FROM public.experiment;
