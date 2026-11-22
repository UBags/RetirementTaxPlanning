# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""conftest.py -- one database per xdist worker, for every test that needs one.

WHY THIS IS SHARED RATHER THAN COPIED. Every other test in this repo is pure,
so `pytest -n 8` is the normal way to run and nobody should have to remember
that some files are different. But the database fixtures drop and rebuild the
`india` schema per test, and eight workers doing that against ONE database drop
it out from under each other: pg_namespace unique violations, deadlocks, and
tables that vanish mid-query.

That was solved once in tests/test_credit_db.py and then not applied to the
next database test file, which reproduced the same failure exactly. Hence here:
a fixture two files can share cannot drift out of step with one of them.

A schema per worker would be lighter, but `india` is hardcoded in the SQL and
in every query in store.py, fund_store.py and api.py -- making it configurable
would be changing production code to suit the tests. A database per worker
changes nothing outside this file.
"""
from __future__ import annotations

import os

import pytest

RTP_TEST_DSN = os.environ.get("RTP_TEST_DSN")


def worker_dsn(dsn=None):
    """The DSN this worker should use, creating its database if needed.

    The databases persist between runs by design: creating them is the slow
    part, and every test rebuilds the schema anyway. Under serial pytest there
    is no worker id and the DSN is returned unchanged.
    """
    dsn = dsn or RTP_TEST_DSN
    if not dsn:
        return None
    wid = os.environ.get("PYTEST_XDIST_WORKER")
    if not wid or "://" in dsn:
        return dsn
    try:
        base = dict(kv.split("=", 1) for kv in dsn.split())
    except ValueError:
        return dsn
    target = f"{base.get('dbname', 'postgres')}_{wid}"

    import psycopg2
    admin = psycopg2.connect(
        " ".join(f"{k}={v}" for k, v in dict(base, dbname="postgres").items()))
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s",
                        (target,))
            if not cur.fetchone():
                try:
                    cur.execute(f'CREATE DATABASE "{target}"')
                except psycopg2.errors.DuplicateDatabase:
                    pass
    finally:
        admin.close()
    return " ".join(f"{k}={v}"
                    for k, v in dict(base, dbname=target).items())


@pytest.fixture(scope="session")
def dsn():
    if not RTP_TEST_DSN:
        pytest.skip("set RTP_TEST_DSN to a PostgreSQL database")
    return worker_dsn()
