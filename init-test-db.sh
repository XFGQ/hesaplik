#!/bin/bash
# Postgres ilk acilisinda calisir. Test veritabanini olusturur.
# Sema yuklemez: tests/conftest.py her kosuda semayi kendisi kurar.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	CREATE DATABASE ${POSTGRES_DB}_test OWNER $POSTGRES_USER;
EOSQL
