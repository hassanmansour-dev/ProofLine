#!/bin/sh
# Runs once, on the first start of an empty data volume. The supervisor's checkpoints and
# the judge log / posted marks each get their own database (docs/INBOX.md, ADR-0023).
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE DATABASE ops_checkpoints;
CREATE DATABASE ops_logs;
SQL
