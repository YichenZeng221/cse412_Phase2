\set ON_ERROR_STOP on
\echo 'Import original ACIMS CSV cells from the checksum-verified ZIP.'
-- Run psql from the project root.
BEGIN;
DO $$
BEGIN
    IF EXISTS (SELECT FROM public.experiment) OR EXISTS (SELECT FROM public.component)
       OR EXISTS (SELECT FROM public.stage) OR EXISTS (SELECT FROM public.machine)
       OR EXISTS (SELECT FROM public.segment) OR EXISTS (SELECT FROM public.transducer)
       OR EXISTS (SELECT FROM public.trajectory) OR EXISTS (SELECT FROM public.observation) THEN
        RAISE EXCEPTION 'Import requires empty project tables; existing data has been preserved.';
    END IF;
END $$;

\copy public.experiment FROM PROGRAM 'python3 data/prepare_data.py experiment' WITH (FORMAT csv, HEADER true)
\copy public.component FROM PROGRAM 'python3 data/prepare_data.py component' WITH (FORMAT csv, HEADER true)
\copy public.stage FROM PROGRAM 'python3 data/prepare_data.py stage' WITH (FORMAT csv, HEADER true)
\copy public.machine FROM PROGRAM 'python3 data/prepare_data.py machine' WITH (FORMAT csv, HEADER true)
\copy public.segment FROM PROGRAM 'python3 data/prepare_data.py segment' WITH (FORMAT csv, HEADER true)
\copy public.transducer FROM PROGRAM 'python3 data/prepare_data.py transducer' WITH (FORMAT csv, HEADER true)
\copy public.trajectory FROM PROGRAM 'python3 data/prepare_data.py trajectory' WITH (FORMAT csv, HEADER true)
\copy public.observation FROM PROGRAM 'python3 data/prepare_data.py observation' WITH (FORMAT csv, HEADER true)

-- Continue identity sequences after the imported IDs.
SELECT setval(pg_get_serial_sequence('public.experiment','experiment_id'), max(experiment_id)) FROM public.experiment;
SELECT setval(pg_get_serial_sequence('public.component','component_id'), max(component_id)) FROM public.component;
SELECT setval(pg_get_serial_sequence('public.stage','stage_id'), max(stage_id)) FROM public.stage;
SELECT setval(pg_get_serial_sequence('public.trajectory','trajectory_id'), max(trajectory_id)) FROM public.trajectory;
SELECT setval(pg_get_serial_sequence('public.observation','observation_id'), max(observation_id)) FROM public.observation;
COMMIT;
ANALYZE;
\echo 'Import committed. Run sql/04_validate.sql and sql/03_demo.sql.'
