from __future__ import annotations

import unittest

import frontend_app as fa


class PodIdleTerminationDecisionTests(unittest.TestCase):
    def test_disabled_timeout_never_terminates(self):
        decision = fa.evaluate_idle_termination(
            now=10_000.0,
            last_activity=0.0,
            pod_status="RUNNING",
            pod_ready=True,
            active_jobs=0,
            timeout_seconds=0,
        )
        self.assertFalse(decision["should_terminate"])
        self.assertEqual(decision["reason"], "disabled")

    def test_pod_not_running_never_terminates(self):
        decision = fa.evaluate_idle_termination(
            now=10_000.0,
            last_activity=0.0,
            pod_status="EXITED",
            pod_ready=False,
            active_jobs=0,
            timeout_seconds=180,
        )
        self.assertFalse(decision["should_terminate"])
        self.assertEqual(decision["reason"], "pod-not-running")

    def test_active_job_blocks_termination(self):
        decision = fa.evaluate_idle_termination(
            now=10_000.0,
            last_activity=0.0,
            pod_status="RUNNING",
            pod_ready=True,
            active_jobs=1,
            timeout_seconds=180,
        )
        self.assertFalse(decision["should_terminate"])
        self.assertTrue(decision["reset_activity"])

    def test_bootstrapping_pod_blocks_termination(self):
        decision = fa.evaluate_idle_termination(
            now=10_000.0,
            last_activity=0.0,
            pod_status="RUNNING",
            pod_ready=False,
            active_jobs=0,
            timeout_seconds=180,
        )
        self.assertFalse(decision["should_terminate"])
        self.assertEqual(decision["reason"], "booting")
        self.assertTrue(decision["reset_activity"])

    def test_unknown_activity_resets_instead_of_terminating(self):
        decision = fa.evaluate_idle_termination(
            now=10_000.0,
            last_activity=None,
            pod_status="RUNNING",
            pod_ready=True,
            active_jobs=0,
            timeout_seconds=180,
        )
        self.assertFalse(decision["should_terminate"])
        self.assertEqual(decision["reason"], "activity-unknown")
        self.assertTrue(decision["reset_activity"])

    def test_recent_activity_reports_remaining_seconds(self):
        decision = fa.evaluate_idle_termination(
            now=10_000.0,
            last_activity=10_000.0 - 50,
            pod_status="RUNNING",
            pod_ready=True,
            active_jobs=0,
            timeout_seconds=180,
        )
        self.assertFalse(decision["should_terminate"])
        self.assertEqual(decision["idle_seconds"], 50.0)
        self.assertEqual(decision["remaining_seconds"], 130.0)

    def test_idle_past_timeout_terminates(self):
        decision = fa.evaluate_idle_termination(
            now=10_000.0,
            last_activity=10_000.0 - 181,
            pod_status="RUNNING",
            pod_ready=True,
            active_jobs=0,
            timeout_seconds=180,
        )
        self.assertTrue(decision["should_terminate"])
        self.assertEqual(decision["reason"], "idle-timeout")
        self.assertEqual(decision["remaining_seconds"], 0.0)


class PodTimingHelperTests(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(fa.format_duration(0), "0s")
        self.assertEqual(fa.format_duration(45), "45s")
        self.assertEqual(fa.format_duration(181), "3m 1s")
        self.assertEqual(fa.format_duration(3725), "1h 2m 5s")
        self.assertEqual(fa.format_duration(None), "")

    def test_parse_pod_started_at_and_runtime(self):
        self.assertIsNotNone(fa.parse_pod_started_at({"startedAt": "2026-10-27T19:00:00Z"}))
        self.assertIsNone(fa.parse_pod_started_at({"startedAt": ""}))
        self.assertIsNone(fa.parse_pod_started_at({}))
        runtime = fa.pod_runtime_seconds({"startedAt": "2026-10-27T19:00:00Z"})
        self.assertIsNotNone(runtime)
        self.assertGreaterEqual(runtime, 0)

    def test_iso_and_local_clock(self):
        self.assertEqual(fa.iso_utc(0), "1970-01-01T00:00:00Z")
        self.assertIsNone(fa.iso_utc(None))
        self.assertIsNotNone(fa.local_clock(0))
        self.assertIsNone(fa.local_clock(None))

    def test_record_pod_run_appends_history(self):
        fa.POD_RUN_HISTORY.clear()
        entry = fa.record_pod_run(
            {"id": "pod-abc", "startedAt": "2026-10-27T19:00:00Z"}, "idle-timeout"
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry["pod_id"], "pod-abc")
        self.assertEqual(entry["reason"], "idle-timeout")
        self.assertIn("duration_display", entry)
        self.assertEqual(fa.POD_RUN_HISTORY[0]["pod_id"], "pod-abc")

        second = fa.record_pod_run(
            {"id": "pod-def", "startedAt": "2026-10-27T20:00:00Z"}, "terminated"
        )
        self.assertEqual(fa.POD_RUN_HISTORY[0]["pod_id"], "pod-def")
        self.assertEqual(fa.POD_RUN_HISTORY[1]["pod_id"], "pod-abc")
        self.assertEqual(second["reason"], "terminated")

    def test_record_pod_run_requires_start_timestamp(self):
        fa.POD_RUN_HISTORY.clear()
        self.assertIsNone(fa.record_pod_run({"id": "pod-x"}, "terminated"))
        self.assertEqual(fa.POD_RUN_HISTORY, [])


class PodStatusPayloadTests(unittest.TestCase):
    def setUp(self):
        fa.POD_RUN_HISTORY.clear()
        fa.POD_ACTIVITY["last_activity_at"] = None
        fa.POD_ACTIVITY["last_reason"] = ""
        fa.POD_ACTIVITY["auto_terminated_at"] = None
        fa.POD_ACTIVITY["auto_terminated_pod_id"] = ""

    def test_idle_payload_contains_contract_fields(self):
        fa.mark_pod_activity("job-submitted")
        payload = fa.pod_idle_payload(True)
        for key in (
            "enabled",
            "timeout_seconds",
            "timeout_display",
            "last_activity_at",
            "active_jobs",
            "idle_seconds",
            "remaining_seconds",
            "auto_terminated_at",
        ):
            self.assertIn(key, payload)
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["timeout_seconds"], fa.POD_IDLE_TIMEOUT_SECONDS)

    def test_status_payload_exposes_idle_and_run_history(self):
        fa.record_pod_run(
            {"id": "pod-abc", "startedAt": "2026-10-27T19:00:00Z"}, "idle-timeout"
        )
        payload = fa.format_pod_status_payload(None, False, [])
        self.assertIn("idle", payload)
        self.assertIn("last_run", payload)
        self.assertIn("run_history", payload)
        self.assertEqual(payload["last_run"]["pod_id"], "pod-abc")

    def test_running_pod_payload_has_runtime_fields(self):
        payload = fa.format_pod_status_payload(
            {
                "id": "pod-abc",
                "name": "ltx25-pod",
                "status": "RUNNING",
                "startedAt": "2026-10-27T19:00:00Z",
            },
            True,
            [],
        )
        pod = payload["pod"]
        self.assertIn("runtime_seconds", pod)
        self.assertIn("runtime_display", pod)
        self.assertEqual(pod["phase"], "READY")


class ActiveJobTrackingTests(unittest.TestCase):
    def setUp(self):
        fa.POD_SUBMIT_JOBS.clear()

    def tearDown(self):
        fa.POD_SUBMIT_JOBS.clear()

    def test_stale_jobs_are_pruned(self):
        fa.POD_SUBMIT_JOBS["fresh"] = {"submitted_at": fa.time.time()}
        fa.POD_SUBMIT_JOBS["stale"] = {
            "submitted_at": fa.time.time() - fa.POD_ACTIVE_JOB_MAX_AGE_SECONDS - 5
        }
        self.assertEqual(fa.active_pod_job_count(), 1)
        self.assertIn("fresh", fa.POD_SUBMIT_JOBS)
        self.assertNotIn("stale", fa.POD_SUBMIT_JOBS)


if __name__ == "__main__":
    unittest.main()
