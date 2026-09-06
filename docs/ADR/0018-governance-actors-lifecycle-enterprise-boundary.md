# ADR-0018: Governance Actors Across the Lifecycle / Enterprise Boundary

## Status

Proposed — **deferred from v2.7.0**.

This ADR is written and not accepted. It records a real gap, the decision not to
close it in v2.7, and the shape the eventual seam should take, so that the next
release starts from a decision rather than from a rediscovery. It should be
accepted together with the single lifecycle schema bump described below.

ADR-0015 already listed "enterprise RBAC enforcement" among the things not in
its scope. This ADR states why that is still true after v2.7, which is a
different reason from v2.6's.

---

# Context

`promote_strategy_version(state, name, version, policy, evidence_id, timestamp)`
takes no actor. `StrategyPromotionRecord` carries `name`, `version`,
`from_stage`, `to_stage`, `reason` and `timestamp` — and no actor field.
`DeploymentRecord` carries `environment`, `release_name`, `version`,
`replaced_version`, `is_rollback` and `timestamp` — and no actor field.

`deploy_strategy_version` accepts `deployed_by: str = ""`, but it reaches only
`DeploymentMetadata`, a note on the *model* version, and only when the strategy
version runs a model at all. `DeploymentMetadata`'s own docstring is explicit
that "this is a *note*, not the deployment record". The append-only ledger,
which the lifecycle calls the only answer to "what is live", records no actor.

So the lifecycle's audit trail answers *what* changed and *when*, and is silent
on *who*.

`alphalab.enterprise` has everything needed to answer it: `Principal`,
`Session`, `AuditEvent` with an `actor_id`, and a complete RBAC implementation
with `define_role`, `grant_role`, `permissions_for`, `has_permission` and
`require_permission`. `git grep "from alphalab.enterprise"` outside the package
itself returns **zero hits**. Thirty-two tests, and no production consumer.

Two docstrings state the separation deliberately. `alphalab.lifecycle`: "The
lifecycle sits *above* `ExecutionPipeline` and is not wired into it."
`alphalab.enterprise`: "Audit logging is a standalone capability; RBAC /
workspace / secret operations do not auto-emit audit events, so those modules
stay single-purpose. Callers record audit entries explicitly."

---

# The decisive constraint

`require_permission(state: EnterpriseState, principal_id: str, permission: str)`
needs an `EnterpriseState`. Calling it from `promote_strategy_version` requires
one of three things:

- **(a)** `LifecycleState` gains an `EnterpriseState` field. This trips
  `tests/regression/test_snapshot_field_coverage.py`, requires the field in
  `LifecycleSnapshot`, and therefore requires a **lifecycle schema decision** —
  the exact thing ADR-0017 concludes v2.7 must avoid.
- **(b)** Every promotion and deployment entry point gains an `EnterpriseState`
  parameter. The package dependency is identical; only the plumbing differs.
- **(c)** The check happens in a caller above both. This is what the current
  architecture already permits, and it requires no ADR at all.

And even the reduced form — recording an actor without enforcing a permission —
adds `actor_id` to `StrategyPromotionRecord` and `DeploymentRecord`, **both of
which are captured in `LifecycleSnapshot`**. A v2.6 payload lacks the key, so
`require(payload, "actor_id")` raises. Recording an actor therefore forces
either a schema bump, after de-aliasing `LIFECYCLE_SNAPSHOT_SCHEMA`, or an
optional decode that gives schema 1 two shapes — which is the silent misread
ADR-0014 introduced `schema_version` to prevent.

There is no version of this change that does not touch persisted governance
records.

---

# Decision

**Defer both the actor record and the RBAC seam out of v2.7.** Land them
together in a later release, in one deliberate lifecycle schema bump that also
carries the richer evidence provenance ADR-0017 defers.

The reasoning is four-fold.

**It is the only v2.7 candidate that touches persisted state.** ADR-0016 has
zero persistence impact, because `InstrumentRegistry` is configuration. ADR-0017
has zero, because the digest is frozen and both types gaining a field are
unpersisted. Admitting governance actors would convert a zero-schema-risk
release into a schema-migration release, for the one item the archaeology ranked
lowest.

**The v2.6 precedent argues for bundling.**
`tests/regression/test_portfolio_snapshot_schema_2.py` exists because adding one
field to one position nearly versioned every event in the system. The lesson is
that schema bumps should be deliberate and batched, not incidental.

**It contradicts two explicit architectural statements.** Reversing them is
legitimate, but it is a decision on its own merits, not a rider on a provenance
release.

**Sequencing is substantive, not merely cautious.** ADR-0016 changes what an
`asset_id` means, and ADR-0017 changes what a `ValidationEvidence` can be
trusted to say. Recording *who* acted is more valuable once *what* they acted on
is authoritative.

---

# The intended seam, when this ADR is accepted

Recorded now so the later release implements a decision rather than improvising
one.

**Principal type.** `enterprise.Principal.principal_id`, carried as a bare `str`
on lifecycle records — a reference, not an embedded object, matching how
`run_id`, `evidence_id` and `policy_id` are already carried.

**Promotion actor persistence.** `StrategyPromotionRecord.actor_id: str = ""`.

**Deployment actor persistence.** `DeploymentRecord.actor_id: str = ""`, so the
actor reaches the append-only ledger rather than stopping at the model note.
`deployed_by` is then either folded into it or explicitly documented as the
model note's own field.

`""` means "not recorded", which is the truthful reading of every v2.6 record.

**Schema.** De-alias `LIFECYCLE_SNAPSHOT_SCHEMA` from `DEFAULT_SCHEMA_VERSION`
to a literal, bump it to `2`, and refuse version 1 — the v2.6 `PortfolioSnapshot`
precedent applied unchanged. ADR-0017 recommends the de-alias as a defensive
no-op in v2.7, which leaves only the bump for this ADR.

**Permission check.** Option (b): an explicit `EnterpriseState` parameter on the
governance entry points, not a field on `LifecycleState`. It keeps
`LifecycleState` free of enterprise data, keeps `LifecycleSnapshot` unchanged by
the *check* — only the *record* changes it — and makes the dependency visible in
the signature rather than hidden inside a state container.

**Audit semantics.** The lifecycle's own records stay authoritative for what,
when and who. `enterprise.AuditEvent` remains the standalone capability its
docstring describes. Two logs, one authority each, rather than one log with two
writers.

**Failure semantics.** `EnterprisePermissionError` propagates unchanged, and
nothing is written before the refusal — matching `deploy_strategy_version`'s
existing contract that "nothing is registered before any of these refusals".

**Backward compatibility.** A v2.6 lifecycle payload is refused with a version
message, not misread. There is no migration path, consistent with
`require_schema_version`.

**Testing invariants.** A promotion records its actor. A restored snapshot
answers "who promoted this". An unpermitted principal is refused and nothing is
written. A v2.6 payload is refused by version rather than silently decoded.

---

# Explicit non-goals

Permanent, and not merely deferred:

- Authentication, credentials, password or key handling. `alphalab.enterprise`
  models session lifecycle only, and that limit stands.
- A full IAM system, an identity provider, or federation.
- Auto-emitted audit events from RBAC, workspace or secret operations.
- Coupling `LifecycleState` to `EnterpriseState` as a field.
- Per-environment promotion policy, which needs an `Environment` type and is a
  separate decision.

---

# Consequences of deferring

Accepted for now. "Who promoted this strategy version, and who deployed it" is
unanswerable from persisted state, and `alphalab.enterprise` remains a complete,
tested package with no production consumer. Anyone needing the answer today must
record it outside AlphaLab.

Gained. v2.7 ships with no schema movement at all, which means v2.6 lifecycle
snapshots restore and verify unchanged, and the release carries no migration
risk. When the schema does move, it moves once and carries both governance
actors and the deferred evidence provenance together.

---

# Alternatives Considered

**Record the actor in v2.7 without enforcing permissions.** Rejected: it still
adds a field to two persisted records and still forces the schema decision. The
enforcement is not what makes this expensive; the persistence is.

**Decode `actor_id` as optional and leave the schema at 1.** Rejected: it gives
schema version 1 two payload shapes, which is the silent misread ADR-0014
introduced `schema_version` to prevent.

**Give `LifecycleState` an `EnterpriseState` field.** Rejected as the seam, per
option (a) above: it puts enterprise data inside the lifecycle snapshot and
couples two packages that are deliberately separate.

**Leave the check to callers permanently, per option (c).** Rejected as a
permanent answer, though it is what v2.7 does in practice: a gate anyone can
bypass by calling the function directly is not a governance control. It is
recorded here as the status quo, not as the destination.

---

# Release impact

None in v2.7 — nothing is implemented. When accepted, this is a minor-version
feature with a persisted-format break: `LIFECYCLE_SNAPSHOT_SCHEMA` moves to 2
and version 1 payloads are refused.
