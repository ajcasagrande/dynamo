# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for topology domain utilities.

Tests the read_topology_domains() function that reads topology from a
Downward API volume file at worker startup for topology-aware KV transfer
routing.

These tests import topology.py directly (bypassing the dynamo package hierarchy)
so they work without GPU, CUDA, or any backend installed.
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0, pytest.mark.pre_merge]

# ---------------------------------------------------------------------------
# Module loading: import topology without triggering the full dynamo package
# (which requires dynamo.llm, CUDA, etc.)
# ---------------------------------------------------------------------------
_TOPOLOGY_PY = Path(__file__).resolve().parents[2] / "utils" / "topology.py"


def _load_topology_module():
    """Load topology.py as a standalone module."""
    spec = importlib.util.spec_from_file_location("topology", _TOPOLOGY_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


topology = _load_topology_module()
read_topology_domains = topology.read_topology_domains


class TestReadTopologyDomains:
    """Tests for read_topology_domains()."""

    def test_returns_empty_when_not_enabled(self, monkeypatch):
        """When DYN_TOPOLOGY_ENABLED is not set, returns empty dict."""
        monkeypatch.delenv("DYN_TOPOLOGY_ENABLED", raising=False)
        assert read_topology_domains() == {}

    def test_returns_empty_when_enabled_false(self, monkeypatch):
        """When DYN_TOPOLOGY_ENABLED=false, returns empty dict."""
        monkeypatch.setenv("DYN_TOPOLOGY_ENABLED", "false")
        assert read_topology_domains() == {}

    def test_returns_empty_when_enabled_empty_string(self, monkeypatch):
        """When DYN_TOPOLOGY_ENABLED='', returns empty dict."""
        monkeypatch.setenv("DYN_TOPOLOGY_ENABLED", "")
        assert read_topology_domains() == {}

    def test_reads_topology_from_file(self, monkeypatch, tmp_path):
        """Reads topology value from Downward API volume file."""
        topology_dir = tmp_path / "topology"
        topology_dir.mkdir()
        (topology_dir / "zone").write_text("us-east-1a")

        monkeypatch.setenv("DYN_TOPOLOGY_ENABLED", "true")
        monkeypatch.setenv("DYN_TOPOLOGY_DOMAIN", "zone")
        monkeypatch.setenv("DYN_TOPOLOGY_MOUNT_PATH", str(topology_dir))

        result = read_topology_domains()
        assert result == {"zone": "us-east-1a"}

    def test_strips_whitespace_from_file_value(self, monkeypatch, tmp_path):
        """Strips whitespace/newlines from the topology file value."""
        topology_dir = tmp_path / "topology"
        topology_dir.mkdir()
        (topology_dir / "zone").write_text("  us-east-1a\n")

        monkeypatch.setenv("DYN_TOPOLOGY_ENABLED", "true")
        monkeypatch.setenv("DYN_TOPOLOGY_DOMAIN", "zone")
        monkeypatch.setenv("DYN_TOPOLOGY_MOUNT_PATH", str(topology_dir))

        result = read_topology_domains()
        assert result == {"zone": "us-east-1a"}

    def test_domain_key_is_lowercased(self, monkeypatch, tmp_path):
        """Domain key is lowercased even if env var has mixed case."""
        topology_dir = tmp_path / "topology"
        topology_dir.mkdir()
        (topology_dir / "zone").write_text("us-east-1a")

        monkeypatch.setenv("DYN_TOPOLOGY_ENABLED", "true")
        monkeypatch.setenv("DYN_TOPOLOGY_DOMAIN", "ZONE")
        monkeypatch.setenv("DYN_TOPOLOGY_MOUNT_PATH", str(topology_dir))

        result = read_topology_domains()
        assert "zone" in result

    def test_enabled_case_insensitive(self, monkeypatch, tmp_path):
        """DYN_TOPOLOGY_ENABLED check is case-insensitive."""
        topology_dir = tmp_path / "topology"
        topology_dir.mkdir()
        (topology_dir / "rack").write_text("rack1")

        monkeypatch.setenv("DYN_TOPOLOGY_ENABLED", "True")
        monkeypatch.setenv("DYN_TOPOLOGY_DOMAIN", "rack")
        monkeypatch.setenv("DYN_TOPOLOGY_MOUNT_PATH", str(topology_dir))

        result = read_topology_domains()
        assert result == {"rack": "rack1"}

    def test_hard_exit_when_domain_env_not_set(self, monkeypatch):
        """Exits when enabled but DYN_TOPOLOGY_DOMAIN is not set."""
        monkeypatch.setenv("DYN_TOPOLOGY_ENABLED", "true")
        monkeypatch.delenv("DYN_TOPOLOGY_DOMAIN", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            read_topology_domains()
        assert exc_info.value.code == 1

    def test_hard_exit_when_topology_file_missing(self, monkeypatch, tmp_path):
        """Exits when enabled but topology file does not exist."""
        topology_dir = tmp_path / "topology"
        topology_dir.mkdir()

        monkeypatch.setenv("DYN_TOPOLOGY_ENABLED", "true")
        monkeypatch.setenv("DYN_TOPOLOGY_DOMAIN", "zone")
        monkeypatch.setenv("DYN_TOPOLOGY_MOUNT_PATH", str(topology_dir))

        with pytest.raises(SystemExit) as exc_info:
            read_topology_domains()
        assert exc_info.value.code == 1

    def test_hard_exit_when_topology_file_empty(self, monkeypatch, tmp_path):
        """Exits when topology file exists but is empty."""
        topology_dir = tmp_path / "topology"
        topology_dir.mkdir()
        (topology_dir / "zone").write_text("")

        monkeypatch.setenv("DYN_TOPOLOGY_ENABLED", "true")
        monkeypatch.setenv("DYN_TOPOLOGY_DOMAIN", "zone")
        monkeypatch.setenv("DYN_TOPOLOGY_MOUNT_PATH", str(topology_dir))

        with pytest.raises(SystemExit) as exc_info:
            read_topology_domains()
        assert exc_info.value.code == 1

    def test_uses_default_mount_path(self, monkeypatch):
        """Uses default mount path when DYN_TOPOLOGY_MOUNT_PATH not set."""
        monkeypatch.setenv("DYN_TOPOLOGY_ENABLED", "true")
        monkeypatch.setenv("DYN_TOPOLOGY_DOMAIN", "zone")
        monkeypatch.delenv("DYN_TOPOLOGY_MOUNT_PATH", raising=False)

        # The default path /etc/dynamo/topology won't exist in test,
        # so this should exit with error (file not found)
        with pytest.raises(SystemExit) as exc_info:
            read_topology_domains()
        assert exc_info.value.code == 1
