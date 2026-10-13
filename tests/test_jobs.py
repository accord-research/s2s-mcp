"""Background jobs: slow recipe runs keep going after the tool call returns."""
import threading

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from s2s_mcp import jobs


def test_fast_job_returns_done_and_leaves_nothing_behind():
    reg = jobs.Registry()
    status, out = reg.run("t", "k1", lambda: {"x": 1}, wait=5)
    assert (status, out) == ("done", {"x": 1})
    assert reg.pending("t") == []


def test_slow_job_reports_running_then_done_without_rerunning():
    reg = jobs.Registry()
    gate, calls = threading.Event(), []

    def slow():
        calls.append(1)
        gate.wait(5)
        return {"x": 2}

    status, info = reg.run("t", "k2", slow, wait=0.05)
    assert status == "running" and info["elapsed_seconds"] >= 0
    assert reg.pending("t") == ["k2"]
    gate.set()
    status, out = reg.run("t", "k2", slow, wait=5)
    assert (status, out) == ("done", {"x": 2})
    assert calls == [1]
    assert reg.pending("t") == []


def test_identical_requests_share_one_job():
    reg = jobs.Registry()
    gate, calls = threading.Event(), []

    def slow():
        calls.append(1)
        gate.wait(5)
        return {"x": 3}

    reg.run("t", "k3", slow, wait=0.05)
    reg.run("t", "k3", slow, wait=0.05)
    gate.set()
    assert reg.run("t", "k3", slow, wait=5) == ("done", {"x": 3})
    assert calls == [1]


def test_tenants_do_not_share_jobs():
    reg = jobs.Registry()
    calls = []
    reg.run("a", "k", lambda: calls.append("a"), wait=5)
    reg.run("b", "k", lambda: calls.append("b"), wait=5)
    assert calls == ["a", "b"]


def test_failure_reaches_the_next_poll_then_clears():
    reg = jobs.Registry()
    gate = threading.Event()

    def bad():
        gate.wait(5)
        raise ToolError("seasonal_mme failed: boom")

    assert reg.run("t", "k4", bad, wait=0.05)[0] == "running"
    gate.set()
    with pytest.raises(ToolError, match="boom"):
        reg.run("t", "k4", bad, wait=5)
    assert reg.run("t", "k4", lambda: {"ok": True}, wait=5) == ("done", {"ok": True})   # fresh start after a failure


def test_unexpected_exceptions_become_tool_errors_without_host_paths():
    reg = jobs.Registry()

    def bad():
        raise ValueError("/Users/someone/secret/path")

    with pytest.raises(ToolError, match="ValueError") as e:
        reg.run("t", "k5", bad, wait=5)
    assert "/Users" not in str(e.value)
