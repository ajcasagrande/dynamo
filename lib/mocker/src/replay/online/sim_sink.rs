// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! [`SimSink`]: the [`RequestSink`] implementation backed by the in-process
//! simulated engine.
//!
//! It dispatches the native [`DirectRequest`] verbatim through the existing
//! online request task, so the simulated path and the live HTTP path share the
//! same dispatch seam without changing simulated scheduling behavior.
//! Measurement for the simulated path continues to flow through `run_demux`, so
//! the observer argument is unused here.

use anyhow::{Result, anyhow};
use async_trait::async_trait;
use uuid::Uuid;

use crate::loadgen::{DispatchRequest, RequestObserver, RequestSink};
use crate::replay::ReplayTerminalStatus;

use super::task::{RequestTaskContext, run_request_task};

/// Dispatches simulated requests through the online request task.
pub(super) struct SimSink {
    pub(super) ctx: RequestTaskContext,
}

#[async_trait]
impl RequestSink for SimSink {
    async fn dispatch(&self, req: DispatchRequest, _obs: &dyn RequestObserver) -> Result<()> {
        let direct = req
            .sim_request
            .ok_or_else(|| anyhow!("SimSink requires a sim_request payload"))?;
        run_request_task(self.ctx.clone(), direct, None).await
    }
}

/// No-op observer for the simulated path, whose measurement is produced by
/// `run_demux` rather than by the sink.
pub(super) struct NoopObserver;

impl RequestObserver for NoopObserver {
    fn on_arrival(&self, _uuid: Uuid, _arrival_ms: f64, _input_length: usize, _requested: usize) {}
    fn on_admit(&self, _uuid: Uuid, _admit_ms: f64, _reused_input_tokens: usize) {}
    fn on_token(&self, _uuid: Uuid, _at_ms: f64) {}
    fn on_terminal(&self, _uuid: Uuid, _status: ReplayTerminalStatus) {}
}
