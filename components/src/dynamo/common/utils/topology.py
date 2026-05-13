# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Topology domain utilities for topology-aware KV transfer routing.

Workers read their topology placement (e.g. zone, rack) from a file
projected by the Kubernetes Downward API volume. The operator's init
container copies the relevant node label onto the pod, and the Downward
API projects it as a file the runtime reads at startup.

The router uses this metadata to constrain KV-cache transfers to workers
within the same topology domain.

Environment variables (set by the operator):
    DYN_TOPOLOGY_ENABLED: Set to "true" to enable topology reading.
    DYN_TOPOLOGY_MOUNT_PATH: Directory where the Downward API volume is
        mounted (default: /etc/dynamo/topology).
    DYN_TOPOLOGY_DOMAIN: The topology domain name to read (e.g. "zone").
        The file at {mount_path}/{domain} contains the topology value.
"""

import logging
import os
import sys
from pathlib import Path

_TOPOLOGY_ENABLED_VAR = "DYN_TOPOLOGY_ENABLED"
_TOPOLOGY_MOUNT_PATH_VAR = "DYN_TOPOLOGY_MOUNT_PATH"
_TOPOLOGY_DOMAIN_VAR = "DYN_TOPOLOGY_DOMAIN"
_DEFAULT_MOUNT_PATH = "/etc/dynamo/topology"

logger = logging.getLogger(__name__)


def read_topology_domains() -> dict[str, str]:
    """Read topology domain labels from the Downward API volume.

    The operator injects three env vars:
      - DYN_TOPOLOGY_ENABLED=true
      - DYN_TOPOLOGY_MOUNT_PATH=/etc/dynamo/topology
      - DYN_TOPOLOGY_DOMAIN=zone

    The topology value is read from the file at {mount_path}/{domain}.

    Returns:
        Dictionary mapping the topology domain to its value
        (e.g. {"zone": "us-east-1a"}). Empty dict if topology is not enabled.

    Raises:
        SystemExit: If DYN_TOPOLOGY_ENABLED=true but the topology file is
            missing, empty, or the domain env var is not set — indicating the
            operator or init container failed to inject topology labels.
    """
    enabled = os.environ.get(_TOPOLOGY_ENABLED_VAR, "").lower()
    if enabled != "true":
        return {}

    domain = os.environ.get(_TOPOLOGY_DOMAIN_VAR, "").strip().lower()
    if not domain:
        logger.error(
            "DYN_TOPOLOGY_ENABLED=true but %s is not set. "
            "The operator must set this to the topology domain name "
            "(e.g. 'zone'). Exiting.",
            _TOPOLOGY_DOMAIN_VAR,
        )
        sys.exit(1)

    mount_path = os.environ.get(_TOPOLOGY_MOUNT_PATH_VAR, _DEFAULT_MOUNT_PATH)
    topology_file = Path(mount_path) / domain

    try:
        value = topology_file.read_text().strip()
    except FileNotFoundError:
        logger.error(
            "DYN_TOPOLOGY_ENABLED=true but topology file %s does not exist. "
            "This indicates the operator's init container failed to copy the "
            "node label onto the pod before the runtime started. Exiting.",
            topology_file,
        )
        sys.exit(1)

    if not value:
        logger.error(
            "DYN_TOPOLOGY_ENABLED=true but topology file %s is empty. "
            "The Downward API volume file exists but contains no value. "
            "This indicates the pod label was not set. Exiting.",
            topology_file,
        )
        sys.exit(1)

    logger.info("Topology domains: {%s: %s} (from %s)", domain, value, topology_file)
    return {domain: value}
