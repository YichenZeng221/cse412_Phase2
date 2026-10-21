\set ON_ERROR_STOP on
\echo 'Creating the Team 19 database if it does not already exist.'

-- Create the database only if it is missing.
SELECT 'CREATE DATABASE cse412_team19'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_database WHERE datname = 'cse412_team19'
)
\gexec

\connect cse412_team19
\echo 'Connected to cse412_team19. Run sql/01_schema.sql next.'
