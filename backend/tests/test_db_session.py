"""Tests for engine construction.

These assert on *configuration*, not on a live database, so they run anywhere.
The timeouts they pin down are the difference between a readiness probe that
answers 503 and one that hangs until the client gives up.
"""

from __future__ import annotations

from app.core.config import Settings
from app.db.session import (
    build_engine,
    build_engine_kwargs,
    get_engine,
    get_sessionmaker,
    statement_timeout_listener,
)


class TestEngineConfiguration:
    def test_bounds_the_time_spent_opening_a_connection(self, settings: Settings) -> None:
        """Regression: without this, an unreachable database hangs the request."""
        kwargs = build_engine_kwargs(settings)

        assert kwargs["connect_args"]["connect_timeout"] == settings.db_connect_timeout_seconds

    def test_sends_no_startup_options(self, settings: Settings) -> None:
        """Regression: `options=-c statement_timeout=...` in the startup packet
        is refused by connection poolers (Neon's pooled endpoint: "unsupported
        startup parameter in options"), so the connection never opens. The
        timeout is applied by a SET after connecting instead."""
        assert "options" not in build_engine_kwargs(settings)["connect_args"]

    def test_applies_the_statement_timeout_after_connecting(self, settings: Settings) -> None:
        """The timeout still exists; it just arrives as a statement."""
        from unittest.mock import MagicMock

        dbapi_connection = MagicMock()
        cursor = dbapi_connection.cursor.return_value.__enter__.return_value

        statement_timeout_listener(settings)(dbapi_connection, MagicMock())

        cursor.execute.assert_called_once_with(
            f"SET statement_timeout = {settings.db_statement_timeout_ms}"
        )

    def test_registers_the_timeout_listener_on_the_pool(self, settings: Settings) -> None:
        engine = build_engine(settings)

        listener_names = [fn.__name__ for fn in engine.pool.dispatch.connect]
        assert "apply_statement_timeout" in listener_names

    def test_bounds_the_wait_for_a_free_pool_slot(self, settings: Settings) -> None:
        assert build_engine_kwargs(settings)["pool_timeout"] == settings.db_pool_timeout_seconds

    def test_checks_connections_are_alive_before_reuse(self, settings: Settings) -> None:
        """Guards against 'connection closed by peer' errors after an idle period."""
        assert build_engine_kwargs(settings)["pool_pre_ping"] is True

    def test_builds_an_engine_pointing_at_the_configured_database(self, settings: Settings) -> None:
        engine = build_engine(settings)

        # `str(engine.url)` masks the password, so render it explicitly to
        # confirm the whole URL survived the round trip rather than just the name.
        assert engine.url.render_as_string(hide_password=False) == str(settings.database_url)


class TestProcessWideFactories:
    def test_engine_is_created_once_per_process(self) -> None:
        assert get_engine() is get_engine()

    def test_sessionmaker_is_bound_to_that_engine(self) -> None:
        assert get_sessionmaker().kw["bind"] is get_engine()
