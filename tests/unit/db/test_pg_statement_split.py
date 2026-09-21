"""``_split_pg_sql`` must not cut a statement in half.

The splitter feeds every ``NNN_*.pg.sql`` file to psycopg one statement at a
time, so a semicolon it mistakes for a terminator ships a syntax error to a
live database. Semicolons that do *not* end a statement live in three places:
``'...'`` literals, ``$tag$ ... $tag$`` bodies, and comments.
"""

from __future__ import annotations

from octop.infra.db.migrate import _split_pg_sql


def test_dollar_quoted_body_stays_one_statement() -> None:
    """A ``DO $$ ... $$`` guard holds semicolons and a nested ``$mirror$`` body."""
    sql = (
        "DO $$\n"
        "BEGIN\n"
        "  IF EXISTS (SELECT 1 FROM information_schema.columns) THEN\n"
        "    EXECUTE $mirror$\n"
        "INSERT INTO a(b) SELECT 'x;y' FROM c;\n"
        "$mirror$;\n"
        "  END IF;\n"
        "END\n"
        "$$;\n"
        "UPDATE _schema_version SET version = 21;\n"
    )

    statements = _split_pg_sql(sql)

    assert len(statements) == 2
    assert statements[0].startswith("DO $$")
    assert statements[0].endswith("$$")
    assert "EXECUTE $mirror$" in statements[0]
    assert statements[1] == "UPDATE _schema_version SET version = 21"


def test_semicolon_inside_a_string_literal_is_not_a_terminator() -> None:
    sql = "INSERT INTO notes(body) VALUES ('a;\nb');\nUPDATE t SET x = 1;\n"

    statements = _split_pg_sql(sql)

    assert len(statements) == 2
    assert statements[0].startswith("INSERT INTO notes(body)")
    assert statements[1] == "UPDATE t SET x = 1"


def test_apostrophe_in_a_comment_does_not_swallow_the_file() -> None:
    """The shipped files say ``SQLite's``; prose must not open a string literal."""
    sql = (
        "-- SQLite's rebuild is a table copy.\nCREATE TABLE t (id INTEGER);\nUPDATE t SET id = 1;\n"
    )

    statements = _split_pg_sql(sql)

    assert len(statements) == 2
    assert statements[0] == "CREATE TABLE t (id INTEGER)"
    assert statements[1] == "UPDATE t SET id = 1"


def test_semicolon_inside_a_block_comment_is_not_a_terminator() -> None:
    sql = "/* what this does;\n   and why */\nCREATE TABLE t (id INTEGER);\n"

    statements = _split_pg_sql(sql)

    assert len(statements) == 1
    assert "CREATE TABLE t (id INTEGER)" in statements[0]


def test_unterminated_dollar_quote_keeps_the_remainder() -> None:
    """Hand psycopg one mangled statement, not a split prefix of one."""
    sql = "DO $$\nBEGIN\n  EXECUTE 'SELECT 1';\nEND\n"

    assert _split_pg_sql(sql) == ["DO $$\nBEGIN\n  EXECUTE 'SELECT 1';\nEND"]
