# Roadmap — ROS 2 world-state query service (PROJECT.md step 4): PR breakdown

**Status:** not started. Recorded 2026-09-19 (D35). This is PROJECT.md step 4:
wrap `robot_world` in a ROS 2 query service so perception (invariant 4) can
write into it and ROS 2 nodes can read it.

**Decided (D35):** the world state — `robot_world`'s pure-Python
`WorldStore`/`FileWorldStore` — is exposed to the ROS 2 graph through a
dedicated query service in a **new package** `robot_world_ros`.
`robot_world` stays pure-Python and ROS-free; `robot_backends`/`robot_mcp`
stay ROS-free at runtime (D30). The service is a thin adapter over the store,
not a second representation — the JSON store stays the single source of truth
(D23). This also closes D23's recorded gap ("no location add/remove API yet")
via the write surface.

## Ground truth this must preserve
- The world document schema (`WorldDocument`, `world_schema_version`) — the
  service serializes the SAME document, never a parallel shape (D23).
- Atomic write semantics (one batch = one write, temp + `os.replace`) stay in
  `robot_world`; the service only calls them.
- `robot_mcp`'s `--backend` seam and D30 are untouched.

## The PRs

### PR1 — srv definitions + read-only query node
- New package `robot_world_ros` with the srv definition(s) for querying the
  world state, and a node that serves the seed/live document through the
  `FileWorldStore`. Pin the interface shape here (srv over topic; small query
  surface, not a kitchen-sink API) and write the rationale.
- **Test:** a query returns the seed world (locations + objects) exactly as the
  store reads it; node builds + responds headless; no `robot_world` mutation.

### PR2 — write path (perception writes into the store)
- Extend the service with the write surface perception needs: update an
  object's pose and add/remove objects (perception's scene JSON → store).
- **Test:** a write persists (atomic), survives a re-read, and the store's
  strict parsing / `world_schema_version` still hold.

### PR3 — launch integration + end-to-end
- Wire the node into `robot_bringup`'s launch (spawn alongside the existing
  sim/control stack) and add an end-to-end test: perception-shaped input lands
  in the store the brain reads.
- **Test:** node spawns under launch; a query→write→query round-trip matches.

## Merge order
```
PR1 ─► PR2 ─► PR3
```
Sequential (same package). One dispatch slot.

## Open risks
- **Interface shape** (srv granularity) is the real design question — PR1 pins
  it with a small srv set + written rationale.
- **Where the node lives** (new package vs `robot_bringup`) — settled in PR1;
  a new package keeps `robot_world` pure, at the cost of one more package.
