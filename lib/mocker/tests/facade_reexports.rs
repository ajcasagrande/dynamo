// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Proves the curated shared-core facade is reachable from outside the crate:
//! a downstream load generator (dynamo-aiperf) can build a `TraceCollector`,
//! feed it measurement events, and produce a report using only the public API.

use dynamo_mocker::loadgen::{DispatchRequest, RequestObserver, RequestSink};
use dynamo_mocker::replay::{ReplayTerminalStatus, TraceCollector};
use uuid::Uuid;

#[test]
fn collector_observer_methods_are_public() {
    let mut c = TraceCollector::default();
    let u = Uuid::nil();
    c.on_arrival(u, 0.0, 4, 8);
    c.on_admit(u, 1.0, 0);
    c.on_token(u, 2.0);
    c.on_token(u, 3.0);
    c.on_terminal(u, ReplayTerminalStatus::Completed);
    let report = c.finish().with_wall_time_ms(10.0);
    // One request, two output tokens observed.
    assert_eq!(report.request_counts.total_output_tokens, 2);
    assert_eq!(report.request_counts.completed_requests, 1);
}

#[test]
fn sink_and_dispatch_request_are_public() {
    // Compile-time proof that the dispatch seam types are nameable/usable
    // downstream. A trivial sink drives a request and the observer counts.
    use std::sync::Mutex;

    #[derive(Default)]
    struct Counter {
        tokens: Mutex<usize>,
    }
    impl RequestObserver for Counter {
        fn on_arrival(&self, _: Uuid, _: f64, _: usize, _: usize) {}
        fn on_admit(&self, _: Uuid, _: f64, _: usize) {}
        fn on_token(&self, _: Uuid, _: f64) {
            *self.tokens.lock().unwrap() += 1;
        }
        fn on_terminal(&self, _: Uuid, _: ReplayTerminalStatus) {}
    }

    struct OneShot;
    #[async_trait::async_trait]
    impl RequestSink for OneShot {
        async fn dispatch(
            &self,
            req: DispatchRequest,
            obs: &dyn RequestObserver,
        ) -> anyhow::Result<()> {
            obs.on_token(req.uuid, 0.0);
            Ok(())
        }
    }

    let obs = Counter::default();
    let req = DispatchRequest {
        uuid: Uuid::nil(),
        input_length: 1,
        max_output_tokens: 1,
        prompt_text: None,
        sim_request: None,
    };
    futures::executor::block_on(OneShot.dispatch(req, &obs)).unwrap();
    assert_eq!(*obs.tokens.lock().unwrap(), 1);
}
