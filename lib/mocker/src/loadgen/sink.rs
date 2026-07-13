// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Transport-neutral dispatch seam shared by the simulated engine (`SimSink`)
//! and real HTTP clients (dynamo-aiperf's `HttpSink`).
//!
//! The scheduling driver drives requests through [`RequestSink`], and
//! measurements flow back through [`RequestObserver`] into a
//! `TraceCollector`. This lets the load-generation core (workload, driver,
//! collector) be reused verbatim across simulated and live transports instead
//! of being re-implemented per transport.

use uuid::Uuid;

use crate::common::protocols::DirectRequest;
use crate::replay::ReplayTerminalStatus;

/// Measurement hook fed by any sink. Timestamps are milliseconds relative to
/// run start.
///
/// TTFT is derived by the collector as the first [`on_token`](RequestObserver::on_token)
/// for a request, so sinks do not emit a separate first-token event.
pub trait RequestObserver: Send + Sync {
    /// Record request arrival with its input length and requested output length.
    fn on_arrival(
        &self,
        uuid: Uuid,
        arrival_ms: f64,
        input_length: usize,
        requested_output_length: usize,
    );
    /// Record admission (scheduling start), with the count of prefix-cache-reused input tokens.
    fn on_admit(&self, uuid: Uuid, admit_ms: f64, reused_input_tokens: usize);
    /// Record one output token observed at `at_ms`.
    fn on_token(&self, uuid: Uuid, at_ms: f64);
    /// Record terminal status for the request.
    fn on_terminal(&self, uuid: Uuid, status: ReplayTerminalStatus);
}

/// Transport-neutral dispatch unit handed to a [`RequestSink`].
///
/// Simulated sinks read `tokens`; HTTP sinks read `prompt_text`. Timing and
/// identity fields are shared across both transports.
#[derive(Debug, Clone)]
pub struct DispatchRequest {
    /// Stable per-request identifier used to correlate observer events.
    pub uuid: Uuid,
    /// Prompt length in tokens, for measurement accounting.
    pub input_length: usize,
    /// Maximum number of output tokens to request.
    pub max_output_tokens: usize,
    /// Prompt text, consumed by HTTP sinks that place bytes on the wire.
    pub prompt_text: Option<String>,
    /// The simulated-engine sink dispatches this native request verbatim,
    /// preserving all scheduling-relevant fields. `None` for HTTP-only requests.
    pub sim_request: Option<DirectRequest>,
}

/// Dispatch one request, drive it to a terminal state, and resolve on
/// completion.
///
/// Implementations emit measurement events through `obs`. `dispatch` returns
/// `Err` only on a transport/dispatch failure the caller should surface; a
/// request that completes with an error terminal status returns `Ok(())` after
/// emitting `obs.on_terminal(..)`.
#[async_trait::async_trait]
pub trait RequestSink: Send + Sync {
    /// Dispatch `req`, awaiting terminal completion.
    async fn dispatch(&self, req: DispatchRequest, obs: &dyn RequestObserver)
    -> anyhow::Result<()>;
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;
    use uuid::Uuid;

    #[derive(Default)]
    struct RecordingObserver {
        tokens: Mutex<Vec<(Uuid, f64)>>,
    }
    impl RequestObserver for RecordingObserver {
        fn on_arrival(&self, _u: Uuid, _a: f64, _i: usize, _o: usize) {}
        fn on_admit(&self, _u: Uuid, _a: f64, _r: usize) {}
        fn on_token(&self, u: Uuid, at: f64) {
            self.tokens.lock().unwrap().push((u, at));
        }
        fn on_terminal(&self, _u: Uuid, _s: ReplayTerminalStatus) {}
    }

    struct EchoSink;
    #[async_trait::async_trait]
    impl RequestSink for EchoSink {
        async fn dispatch(
            &self,
            req: DispatchRequest,
            obs: &dyn RequestObserver,
        ) -> anyhow::Result<()> {
            obs.on_arrival(req.uuid, 0.0, req.input_length, req.max_output_tokens);
            for i in 0..req.max_output_tokens {
                obs.on_token(req.uuid, i as f64);
            }
            obs.on_terminal(req.uuid, ReplayTerminalStatus::Completed);
            Ok(())
        }
    }

    #[tokio::test]
    async fn sink_emits_one_token_per_output() {
        let obs = RecordingObserver::default();
        let req = DispatchRequest {
            uuid: Uuid::nil(),
            input_length: 3,
            max_output_tokens: 5,
            prompt_text: None,
            sim_request: None,
        };
        EchoSink.dispatch(req, &obs).await.unwrap();
        assert_eq!(obs.tokens.lock().unwrap().len(), 5);
    }
}
