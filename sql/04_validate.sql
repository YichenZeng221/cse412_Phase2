\set ON_ERROR_STOP on
\pset pager off
\echo 'Validate imported source data, relational integrity, and constraint behavior.'

-- Explicit test IDs leave identity sequences unchanged.
BEGIN;

SELECT 'experiment' AS table_name, count(*) AS actual_rows, 18::BIGINT AS expected_rows FROM public.experiment
UNION ALL SELECT 'component', count(*), 28 FROM public.component
UNION ALL SELECT 'stage', count(*), 2 FROM public.stage
UNION ALL SELECT 'machine', count(*), 12 FROM public.machine
UNION ALL SELECT 'segment', count(*), 6 FROM public.segment
UNION ALL SELECT 'transducer', count(*), 7 FROM public.transducer
UNION ALL SELECT 'trajectory', count(*), 7706 FROM public.trajectory
UNION ALL SELECT 'observation', count(*), 3235437 FROM public.observation;

DO $validation$
DECLARE
    item RECORD;
    actual BIGINT;
    project_tables OID[];
BEGIN
    IF (SELECT array_agg(c.relname::TEXT ORDER BY c.relname)
        FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p'))
        IS DISTINCT FROM ARRAY['component','experiment','machine','observation',
                               'segment','stage','trajectory','transducer']::TEXT[] THEN
        RAISE EXCEPTION 'Expected exactly the eight project tables in public.';
    END IF;
    SELECT array_agg(c.oid) INTO project_tables
    FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p');

    FOR item IN SELECT * FROM (VALUES
        ('experiment',18::BIGINT), ('component',28), ('stage',2), ('machine',12),
        ('segment',6), ('transducer',7), ('trajectory',7706), ('observation',3235437)
    ) AS expected(table_name, row_count) LOOP
        EXECUTE format('SELECT count(*) FROM public.%I', item.table_name) INTO actual;
        IF actual <> item.row_count THEN
            RAISE EXCEPTION 'Row count for %: expected %, found %.',
                item.table_name, item.row_count, actual;
        END IF;
    END LOOP;
    RAISE NOTICE 'PASS: all eight table counts match the independent archive audit.';

    IF (SELECT count(*) FROM pg_constraint
        WHERE conrelid = ANY(project_tables) AND contype = 'p') <> 8
       OR (SELECT count(*) FROM pg_constraint
           WHERE conrelid = ANY(project_tables) AND contype = 'f' AND convalidated) <> 7 THEN
        RAISE EXCEPTION 'Expected eight primary keys and seven validated foreign keys.';
    END IF;
    IF (SELECT data_type FROM information_schema.columns
        WHERE table_schema='public' AND table_name='observation' AND column_name='logical_time')
        IS DISTINCT FROM 'numeric'
       OR (SELECT data_type FROM information_schema.columns
           WHERE table_schema='public' AND table_name='observation' AND column_name='value')
        IS DISTINCT FROM 'text' THEN
        RAISE EXCEPTION 'Observation must use NUMERIC logical_time and TEXT value.';
    END IF;
    IF (SELECT count(*) FROM information_schema.columns
        WHERE table_schema='public' AND is_identity='YES'
          AND identity_generation='BY DEFAULT') <> 5 THEN
        RAISE EXCEPTION 'Expected five independent identity keys; subtypes inherit Component keys.';
    END IF;
    RAISE NOTICE 'PASS: eight primary keys, seven foreign keys, and declared source data types.';

    IF EXISTS (SELECT FROM public.stage s LEFT JOIN public.component c USING (component_id)
               WHERE s.component_id IS NOT NULL AND c.component_id IS NULL)
       OR EXISTS (SELECT FROM public.machine m LEFT JOIN public.component c USING (component_id)
                  WHERE c.component_id IS NULL)
       OR EXISTS (SELECT FROM public.segment s LEFT JOIN public.component c USING (component_id)
                  WHERE c.component_id IS NULL)
       OR EXISTS (SELECT FROM public.transducer d LEFT JOIN public.component c USING (component_id)
                  WHERE c.component_id IS NULL)
       OR EXISTS (SELECT FROM public.trajectory t LEFT JOIN public.experiment e USING (experiment_id)
                  LEFT JOIN public.component c USING (component_id)
                  WHERE e.experiment_id IS NULL OR c.component_id IS NULL)
       OR EXISTS (SELECT FROM public.observation o LEFT JOIN public.trajectory t USING (trajectory_id)
                  WHERE t.trajectory_id IS NULL) THEN
        RAISE EXCEPTION 'An orphan row was found.';
    END IF;
    RAISE NOTICE 'PASS: no orphan records in any foreign-key relationship.';

    IF (SELECT count(*) FROM public.observation WHERE logical_time IS NULL) <> 4554
       OR (SELECT count(*) FROM public.experiment WHERE simulation_steps IS NULL) <> 18
       OR (SELECT count(*) FROM public.stage WHERE component_id IS NULL) <> 2 THEN
        RAISE EXCEPTION 'Missing-source metadata counts differ from the reviewed archive mapping.';
    END IF;
    IF EXISTS (SELECT FROM public.observation
               WHERE logical_time < 0 OR logical_time::TEXT IN ('NaN','Infinity','-Infinity'))
       OR EXISTS (SELECT FROM public.observation WHERE value IS NULL OR value = '') THEN
        RAISE EXCEPTION 'An invalid timestamp or empty observation value was found.';
    END IF;
    IF EXISTS (SELECT trajectory_id FROM public.observation GROUP BY trajectory_id
               HAVING count(*) FILTER (WHERE logical_time IS NULL) > 1
                  OR min(observation_id) FILTER (WHERE logical_time IS NULL) > min(observation_id)) THEN
        RAISE EXCEPTION 'A missing timestamp occurs after a trajectory initial-state observation.';
    END IF;
    IF (SELECT jsonb_object_agg(trajectory_type, amount)
        FROM (SELECT trajectory_type,count(*) AS amount
              FROM public.trajectory GROUP BY trajectory_type) AS kinds)
        IS DISTINCT FROM '{"numeric":4106,"categorical":448,"message":3058,"empty":94}'::JSONB THEN
        RAISE EXCEPTION 'Trajectory classifications differ from the independent source audit.';
    END IF;
    IF EXISTS (SELECT FROM public.trajectory t JOIN public.observation o USING (trajectory_id)
               WHERE t.trajectory_type='empty') THEN
        RAISE EXCEPTION 'An empty source trajectory unexpectedly contains observations.';
    END IF;
    IF (SELECT array_agg(c.component_name ORDER BY c.component_name)
        FROM public.component c
        LEFT JOIN public.machine m USING (component_id)
        LEFT JOIN public.segment s USING (component_id)
        LEFT JOIN public.transducer d USING (component_id)
        WHERE m.component_id IS NULL AND s.component_id IS NULL AND d.component_id IS NULL)
        IS DISTINCT FROM ARRAY['ga','gpa','gtw']::TEXT[] THEN
        RAISE EXCEPTION 'Expected ga/gpa/gtw to remain base Components without an invented subtype.';
    END IF;
    RAISE NOTICE 'PASS: 4554 initial-state NULL times, 18 unknown step counts, two optional Stage links, and three base-only generators.';
    RAISE NOTICE 'PASS: trajectory types numeric=4106, categorical=448, message=3058, empty=94; empty source columns retained.';

    IF EXISTS (SELECT FROM public.experiment
               WHERE experiment_code !~ '^[0-9]+-[0-9]+-[0-9]+-[0-9]+$'
                  OR pa_count <> split_part(experiment_code,'-',1)::INTEGER
                  OR pb_count <> split_part(experiment_code,'-',2)::INTEGER
                  OR tw_count <> split_part(experiment_code,'-',3)::INTEGER) THEN
        RAISE EXCEPTION 'Experiment pa/pb/tw metadata does not match the source folder labels.';
    END IF;
    WITH latest AS (
        SELECT DISTINCT ON (t.experiment_id,t.variable_name)
            t.experiment_id, t.variable_name, o.value::NUMERIC AS source_value
        FROM public.trajectory t
        JOIN public.component c USING (component_id)
        JOIN public.observation o USING (trajectory_id)
        WHERE c.component_name='tr' AND t.variable_name IN ('pa','pb','tw')
        ORDER BY t.experiment_id,t.variable_name,
                 o.logical_time DESC NULLS LAST,o.observation_id DESC
    )
    SELECT count(*) INTO actual
    FROM latest l JOIN public.experiment e USING (experiment_id)
    WHERE l.source_value = CASE l.variable_name
        WHEN 'pa' THEN e.pa_count WHEN 'pb' THEN e.pb_count WHEN 'tw' THEN e.tw_count END;
    IF actual <> 54 THEN
        RAISE EXCEPTION 'Expected all 54 final tr.pa/pb/tw values to confirm the 18 folder labels; found %.', actual;
    END IF;
    RAISE NOTICE 'PASS: all 18 configuration labels agree with their final tr.pa/pb/tw source observations.';
END;
$validation$;

SELECT trajectory_type, count(*) AS trajectories
FROM public.trajectory GROUP BY trajectory_type ORDER BY trajectory_type;

-- Catch the expected constraint error; fail if the write succeeds.
DO $negative_tests$
DECLARE
    test RECORD;
    rejected BOOLEAN;
BEGIN
    FOR test IN SELECT * FROM (VALUES
        ('duplicate primary key', $q$INSERT INTO public.observation VALUES (1,1,0,'validation')$q$),
        ('duplicate experiment code', $q$INSERT INTO public.experiment VALUES (190000001,(SELECT experiment_code FROM public.experiment WHERE experiment_id=1),0,0,0,NULL)$q$),
        ('duplicate trajectory natural key', $q$INSERT INTO public.trajectory SELECT 190000001,experiment_id,component_id,trajectory_type,variable_name FROM public.trajectory WHERE trajectory_id=1$q$)
    ) AS cases(label, statement) LOOP
        rejected := FALSE;
        BEGIN EXECUTE test.statement;
        EXCEPTION WHEN unique_violation THEN rejected := TRUE;
        END;
        IF NOT rejected THEN RAISE EXCEPTION 'FAILED: % was accepted.', test.label; END IF;
        RAISE NOTICE 'PASS: PostgreSQL rejected %.', test.label;
    END LOOP;

    FOR test IN SELECT * FROM (VALUES
        ('Stage orphan', $q$UPDATE public.stage SET component_id=190000001 WHERE stage_id=1$q$),
        ('Machine orphan', $q$INSERT INTO public.machine VALUES (190000001)$q$),
        ('Segment orphan', $q$INSERT INTO public.segment VALUES (190000001)$q$),
        ('Transducer orphan', $q$INSERT INTO public.transducer VALUES (190000001)$q$),
        ('Trajectory experiment orphan', $q$INSERT INTO public.trajectory VALUES (190000001,190000001,1,'validation','orphan')$q$),
        ('Trajectory component orphan', $q$INSERT INTO public.trajectory VALUES (190000001,1,190000001,'validation','orphan')$q$),
        ('Observation orphan', $q$INSERT INTO public.observation VALUES (190000001,190000001,0,'validation')$q$),
        ('deletion of a referenced trajectory', $q$DELETE FROM public.trajectory WHERE trajectory_id=1$q$)
    ) AS cases(label, statement) LOOP
        rejected := FALSE;
        BEGIN EXECUTE test.statement;
        EXCEPTION WHEN foreign_key_violation THEN rejected := TRUE;
        END;
        IF NOT rejected THEN RAISE EXCEPTION 'FAILED: % was accepted.', test.label; END IF;
        RAISE NOTICE 'PASS: PostgreSQL rejected %.', test.label;
    END LOOP;

    FOR test IN SELECT * FROM (VALUES
        ('negative logical time', $q$INSERT INTO public.observation VALUES (190000001,1,-1,'validation')$q$),
        ('infinite logical time', $q$INSERT INTO public.observation VALUES (190000001,1,'Infinity','validation')$q$),
        ('NaN logical time', $q$INSERT INTO public.observation VALUES (190000001,1,'NaN','validation')$q$),
        ('negative experiment count', $q$INSERT INTO public.experiment VALUES (190000001,'validation_negative_count',-1,0,0,NULL)$q$),
        ('zero simulation steps', $q$INSERT INTO public.experiment VALUES (190000001,'validation_zero_steps',0,0,0,0)$q$),
        ('blank experiment code', $q$INSERT INTO public.experiment VALUES (190000001,'   ',0,0,0,NULL)$q$),
        ('empty observation value', $q$INSERT INTO public.observation VALUES (190000001,1,0,'')$q$)
    ) AS cases(label, statement) LOOP
        rejected := FALSE;
        BEGIN EXECUTE test.statement;
        EXCEPTION WHEN check_violation THEN rejected := TRUE;
        END;
        IF NOT rejected THEN RAISE EXCEPTION 'FAILED: % was accepted.', test.label; END IF;
        RAISE NOTICE 'PASS: PostgreSQL rejected %.', test.label;
    END LOOP;

    FOR test IN SELECT * FROM (VALUES
        ('NULL observation value', $q$INSERT INTO public.observation VALUES (190000001,1,0,NULL)$q$),
        ('NULL trajectory experiment', $q$INSERT INTO public.trajectory VALUES (190000001,NULL,1,'validation','required_fk')$q$)
    ) AS cases(label, statement) LOOP
        rejected := FALSE;
        BEGIN EXECUTE test.statement;
        EXCEPTION WHEN not_null_violation THEN rejected := TRUE;
        END;
        IF NOT rejected THEN RAISE EXCEPTION 'FAILED: % was accepted.', test.label; END IF;
        RAISE NOTICE 'PASS: PostgreSQL rejected %.', test.label;
    END LOOP;
END;
$negative_tests$;

DO $positive_tests$
BEGIN
    -- Roll back these test rows with a custom exception.
    BEGIN
        INSERT INTO public.component VALUES (190000002,'validation_overlap'),
                                             (190000003,'validation_base_only');
        INSERT INTO public.machine VALUES (190000002);
        INSERT INTO public.segment VALUES (190000002);
        INSERT INTO public.transducer VALUES (190000002);
        IF NOT EXISTS (SELECT FROM public.machine m JOIN public.segment s USING (component_id)
                       JOIN public.transducer d USING (component_id) WHERE m.component_id=190000002)
           OR NOT EXISTS (SELECT FROM public.component c
                          WHERE c.component_id=190000003
                            AND NOT EXISTS (SELECT FROM public.machine WHERE component_id=c.component_id)
                            AND NOT EXISTS (SELECT FROM public.segment WHERE component_id=c.component_id)
                            AND NOT EXISTS (SELECT FROM public.transducer WHERE component_id=c.component_id)) THEN
            RAISE EXCEPTION 'FAILED: partial/overlapping ISA was not preserved.';
        END IF;

        UPDATE public.stage SET component_id=190000002;
        IF (SELECT count(*) FROM public.stage WHERE component_id=190000002) <> 2 THEN
            RAISE EXCEPTION 'FAILED: multiple Stages must be allowed to refer to one Component.';
        END IF;
        UPDATE public.stage SET component_id=NULL;

        INSERT INTO public.observation VALUES
            (190000002,1,0,'validation_a'), (190000003,1,0,'validation_b'),
            (190000004,1,NULL,'validation_initial');
        IF (SELECT count(*) FROM public.observation WHERE observation_id IN (190000002,190000003)
            AND trajectory_id=1 AND logical_time=0) <> 2 THEN
            RAISE EXCEPTION 'FAILED: repeated logical times must be permitted.';
        END IF;
        RAISE EXCEPTION USING ERRCODE='PZ001', MESSAGE='Roll back successful validation fixtures.';
    EXCEPTION WHEN SQLSTATE 'PZ001' THEN NULL;
    END;
    IF EXISTS (SELECT FROM public.component WHERE component_id IN (190000002,190000003))
       OR EXISTS (SELECT FROM public.observation WHERE observation_id BETWEEN 190000002 AND 190000004)
       OR EXISTS (SELECT FROM public.stage WHERE component_id IS NOT NULL) THEN
        RAISE EXCEPTION 'FAILED: validation fixtures were not rolled back.';
    END IF;
    RAISE NOTICE 'PASS: partial/overlapping ISA, optional Stage links, repeated times, and NULL source times are accepted.';
    RAISE NOTICE 'PASS: successful fixtures were rolled back without advancing identity sequences.';
END;
$positive_tests$;

ROLLBACK;
\echo 'PASS: all validation assertions completed; original data and identity sequences preserved.'
