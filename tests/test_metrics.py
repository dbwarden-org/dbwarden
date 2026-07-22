from __future__ import annotations

import os
from unittest.mock import patch

from dbwarden.metrics import _parse_version


class TestParseVersion:
    def test_numeric_version(self):
        assert _parse_version("42") == 42.0

    def test_semver(self):
        assert _parse_version("1.2.3") == 1.0

    def test_prefix(self):
        assert _parse_version("v2") == 2.0

    def test_no_match(self):
        assert _parse_version("abc") == 0.0


class TestNoop:
    def test_noop(self):
        from dbwarden.metrics import _noop

        assert _noop() is None
        assert _noop(1, 2, key="val") is None


class TestMetricsEnabled:
    def test_default_disabled(self):
        from dbwarden.metrics import metrics_enabled

        assert metrics_enabled() is False

    def test_enabled_with_env(self):
        from dbwarden.metrics import metrics_enabled

        with patch.dict(os.environ, {"DBWARDEN_METRICS": "true"}):
            assert metrics_enabled() is True

    def test_enabled_with_1(self):
        from dbwarden.metrics import metrics_enabled

        with patch.dict(os.environ, {"DBWARDEN_METRICS": "1"}):
            assert metrics_enabled() is True

    def test_enabled_with_yes(self):
        from dbwarden.metrics import metrics_enabled

        with patch.dict(os.environ, {"DBWARDEN_METRICS": "yes"}):
            assert metrics_enabled() is True
