"""High-performance benchmark suite for the Enterprise layer."""

import time

from alphalab.enterprise import (
    EnterpriseState,
    compliance_report,
    define_role,
    grant_role,
    has_permission,
    open_session,
    record_audit,
    register_principal,
    resolve_session,
)


def run_benchmark() -> None:
    state = EnterpriseState()
    state = define_role(state, "researcher", ["research.run", "research.read", "workspace.join"])

    N_PRINCIPALS = 20_000
    print(f"Starting Enterprise Benchmark: registering {N_PRINCIPALS} principals...")
    start = time.perf_counter()
    for i in range(N_PRINCIPALS):
        state, _ = register_principal(state, f"p{i}", f"Principal {i}", timestamp=float(i))
    duration = time.perf_counter() - start
    print(f"  register_principal: {duration:.4f}s, {N_PRINCIPALS / duration:.2f} ops/sec")

    start = time.perf_counter()
    for i in range(N_PRINCIPALS):
        state = grant_role(state, f"p{i}", "researcher")
    duration = time.perf_counter() - start
    print(f"  grant_role: {duration:.4f}s, {N_PRINCIPALS / duration:.2f} ops/sec")

    N_CHECK = 100_000
    start = time.perf_counter()
    for i in range(N_CHECK):
        has_permission(state, f"p{i % N_PRINCIPALS}", "research.run")
    duration = time.perf_counter() - start
    print(f"  has_permission: {duration:.4f}s, {N_CHECK / duration:.2f} ops/sec")

    N_SESSION = 20_000
    start = time.perf_counter()
    for i in range(N_SESSION):
        state, session = open_session(state, f"p{i}", timestamp=float(i), ttl_seconds=3_600.0)
        resolve_session(state, session.session_id, now=float(i) + 1.0)
    duration = time.perf_counter() - start
    print(f"  open+resolve session: {duration:.4f}s, {N_SESSION / duration:.2f} pairs/sec")

    N_AUDIT = 20_000
    start = time.perf_counter()
    for i in range(N_AUDIT):
        state, _ = record_audit(state, "p0", "benchmark.tick", f"p{i}", timestamp=float(i))
    duration = time.perf_counter() - start
    print(f"  record_audit: {duration:.4f}s, {N_AUDIT / duration:.2f} ops/sec")

    N_REPORT = 200
    start = time.perf_counter()
    for _ in range(N_REPORT):
        compliance_report(state, now=1e9)
    duration = time.perf_counter() - start
    print(
        f"  compliance_report ({N_PRINCIPALS} principals): {duration:.4f}s, "
        f"{N_REPORT / duration:.2f} ops/sec"
    )


if __name__ == "__main__":
    run_benchmark()
