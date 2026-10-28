# Features & Services roadmap

The initial feature registry is deliberately an activation-policy layer. It
keeps service data and identities in place when disabled, records a rollback
snapshot before an apply, and has no Docker socket or host privileges. Core
Reticulum is mandatory. The current optional services are Messaging, Test Echo
Bot, and Nomad Browser; their enabled settings are updated atomically by the
management agent.

## Next milestones

1. **Container lifecycle adapter.** Add an allowlisted host-side controller
   that consumes the registry, starts/stops only known Compose profiles, waits
   for health checks, and restores the prior registry and service state on a
   failed transition. It must never remove service volumes on disable.
2. **LXMF reliable queue and propagation hosting.** Add a separate optional
   propagation-host feature only after its persistent queue, disk quotas,
   operator notices, health/readiness checks, and abuse boundaries are
   specified. It is intentionally unavailable in this change.
3. **RNode integration.** Treat an RNode as a Reticulum-compatible radio
   modem/interface (not a MeshCore companion): require explicit serial/device
   selection, least-privilege device access, configuration preview/rollback,
   and physical-link diagnostics. It is intentionally unavailable here.
4. **Nomad Browser rendering and hosting.** First improve safe Micron layout,
   links, pagination, resource/error diagnostics, and visual regression tests.
   Hosting must be a distinct opt-in service with separate identity, content
   root, size limits, and review; no remote page execution or form submission.

No Windows installation, adoption, connect-flow, production deployment, tag,
or release work is included in this branch.
