# i150 physical-world — red-team (PR A of #150)

Adversarial review of `feat/i150-physical-world` @ `e0cfb64` vs `origin/main`
`3055083`. Method: **executed** probes on the worktree (in-process MuJoCo 3.12,
the sim's own `write_mjcf_model` path). Every finding is labelled VERIFIED (with
the command/output) or UNVERIFIED. Read-only on source/tests; the worktree is
left clean.

This file records two passes:

* **Pass 1** on `acf84ec` — found **BLOCK-1** + 5 NOTEs.
* **Pass 2 (final N+1 clean-pass confirmation)** on `e0cfb64` — BLOCK-1 fix
  verified; **0 BLOCKs remain**.

Attack surface = the manager's 6 falsifiable claims + the 8 review axes in the
task. Summary first, then the evidence.

## Final verdict (pass 2 @ `e0cfb64`)

- **BLOCK: 0.** BLOCK-1 is **RESOLVED** — the fix is correct *and* genuinely
  sensitive (the real test function now FAILS on the ghost model, PASSES on the
  real one). No new BLOCK found.
- **NOTE: 6** — the 5 from pass 1 (NOTE-1 now also resolved; NOTE-2/-3/-4/-5
  stand) plus **NOTE-6** (new, below): the contact-pair assertion is insensitive
  to a *single* zeroed contype/conaffinity bit, though the suite's XML-marker
  test catches that case.
- **N+1 clean pass: CONFIRMED.** Full physics suite (10 tests) green on 3
  consecutive runs; the bringup structural test green; the 5 offending ghost
  variants that must fail do fail (real ghost, support-moved, support-5mm-low).
- The manager's 6 falsifiable claims all **survive**: rest, inertia, geometry
  mirror, support clearance, launch materialization and graceful degradation.
- **Suite status caveat (environment, not the PR):** see NOTE-5 — the full
  `pixi run test` still needs the sim plugin built; in *this* worktree the plugin
  is now vcs-imported/rebuilt, so the PR's own tests are reproducible green.

---

## BLOCK — none remain

### BLOCK-1 — RESOLVED (pass 2)

**Prior finding (pass 1, @ `acf84ec`):**
`test_applied_perturbation_moves_the_object` was an inadequate test: its only
assertion (an `xfrc_applied` push moves the body) passed on the exact ghost body
(`contype="0" conaffinity="0"`) it claimed to catch, because `xfrc_applied` is a
direct DOF force applied regardless of contact. VERIFIED then: the ghost
free-falls through the surface to z≈−77 and the push still moved it 5.3 m.

**Fix (`e0cfb64`):** the test now also asserts a resting contact pair exists —
some contact in `data.contact[:data.ncon]` pairs `obj_1_geom` with
`world_support_geom` — before the push, and the docstring was rewritten to label
the push as proving *movability only*.

**Re-verification (pass 2):** RESOLVED and sensitive. Method: monkey-patched the
test module's `_materialize` to inject the ghost on a throwaway /tmp model and
ran the **real test function** (not a re-implementation):

```
REAL model:      z=1.1500  CONTACT_PRESENT=True
contype=0
 conaffinity=0:  z=-77.2908  CONTACT_PRESENT=False
GHOST: test FAILED  <-- GOOD (sensitive):
  object is not in resting contact with the support surface; the geom has
  contype/conaffinity disabled (or it fell through)
```

So the named test now fails on broken code and passes on the real model — the
rubric's BLOCK condition is cleared. The docstring's two-part claim is now
accurate: the contact-pair assertion is the real proof; the push is honestly
labelled movability-only.

Note the fix is sensitive to the *defined* ghost (both `contype="0"` **and**
`conaffinity="0"` together — what `world_bodies`/`robot_backends` call "ghost"),
which is the failure mode that matters. Its narrowness to a *single* zeroed bit
is a NOTE, not a BLOCK (see NOTE-6) — the suite's XML-marker test covers that
case.

---

## NOTES

- **NOTE-1 — RESOLVED (pass 2).** The rest test's tolerance was 1 cm and a 5 mm
  fall-through slipped through. Fix tightened it to **1e-3** (x/y `atol` and z),
  and the fix commit's claim is VERIFIED: the real settle error is
  dz≈2.3e-5 m (x/y ≈1e-7 m) for mug/book/bowl over 20 000 steps — a **~43x
  margin** over 1e-3, while a 5 mm fall-through now FAILS:
  ```
  support 5 mm low: FAIL -> (1.1449769, 1.15)
  ```
  **Non-flaky:** the rest test passed 5/5 consecutive runs, and the full suite
  passed 3/3 full-file runs, all at 1e-3. No relaxation needed. Resolved.

- **NOTE-6 (new, pass 2) — the contact-pair assertion is insensitive to a
  *single* zeroed contype/conaffinity bit.** VERIFIED: a body with only
  `contype="0"` (conaffinity default) or only `conaffinity="0"` (contype default)
  still forms the object↔support contact pair, so the contact test still PASSES
  (`contact=True`, object rests at z=1.15). MuJoCo's rule is
  `(contype1 & conaffinity2) | (contype2 & conaffinity1)` — the *other* body's
  bits on the far side of the pair satisfy it, so zeroing one bit on the object
  (or on the support) alone does not remove the contact. **Not blocking:** the
  failure the test targets is the *ghost marker pair* (`contype="0"
  conaffinity="0"` together, both bits off), which IS caught; and the suite's
  `test_graspable_object_xml_is_physically_real` catches each single-bit case by
  grepping the literal markers (VERIFIED: it FAILS on `contype=0`-only and on
  `conaffinity=0`-only). So the *suite* is not fooled. The docstring's shorthand
  "(contype=0)" describes a body that is *both* flags off (a genuinely
  collision-inert, free-falling body); taken literally it over-claims. Optional
  hardening: assert `geom_contype[obj_gid] != 0 and geom_conaffinity[obj_gid] != 0`
  from the compiled model, which would make the test self-sufficient. Follow-up
  strength, not a blocker.

- **NOTE-2 — object id colliding with the reserved support body name makes the
  model uncompilable.** `SUPPORT_BODY_NAME = 'world_support'`; an object whose
  `object_id` is `world_support` yields a duplicate body name and `mjModel`
  refuses to compile. RE-VERIFIED at `e0cfb64`: `XML Error: Error: repeated name
  'world_support' in body`. Also, any two colliding ids produce the same
  failure. `_world_bodies_xml` catches *parse* errors but a compile error
  happens later in the plugin, so the sim would abort at load. Cheap fix: prefix
  spliced names (e.g. `world_<id>`) or validate. Low likelihood (store-authored
  ids), hence NOTE.

- **NOTE-3 — the "static" non-graspable bodies are not mass-free; MuJoCo
  auto-derives it.** VERIFIED (pass 1): the counter body has no joint (genuinely
  static/welded — correct), but `body_mass = 324 kg` from its geom volume ×
  default density. Harmless for a static body, and R4 asks only for "no joint,
  no inertial" (the XML has neither). Recorded so nobody is surprised by a 324 kg
  counter.

- **NOTE-4 — the launch test is a text-level grep, not a compile/physics
  check.** `test_materialized_mjcf_contains_the_world_objects` asserts
  `<body name="…">` substrings and `name="world_support"` in the materialized
  text; it never compiles. That is the documented split (physics proof lives in
  `robot_description`), so it is adequate for a structural smoke — but note it
  would pass on text that does not compile (e.g. NOTE-2's collision). Not a
  blocker; the description package's compile-bearing tests cover the load path.

- **NOTE-5 (environment, not a PR defect) — history of the suite not going green
  in an unbuilt worktree.** In pass 1, `pixi run test` could not go green because
  `mujoco_ros2_control` (source-build via `robot.repos`, D33) was absent and
  robot_bringup's sim integration smokes skipped below the raised baseline. In
  pass 2 the plugin is **vcs-imported + rebuilt in this worktree**, so the PR's
  own tests are reproducible: `test_world_bodies.py` 10/10 green and
  `test_materialized_mjcf_contains_the_world_objects` green. The baseline bump
  18→19 remains **correct** (PR adds exactly one bringup test). The remaining
  sim-smoke dependency is an environment property, not a PR defect.

---

## Falsifiable-claim attack results (all survivor claims VERIFIED)

1. **Rest in the full derived model.** VERIFIED. A mug seeded at
   `SUPPORT_TOP_Z + half-height` settles at `z=1.14998` (seed 1.150, Δ≈2e-5)
   with x/y fixed and no NaN after 20 000 steps; box and cylinder shapes settle
   Δ≈0. **Claim holds** (now at the tightened 1e-3 bound — see NOTE-1).

2. **It really collides.** VERIFIED at `e0cfb64`. The object's geom forms a
   live resting contact pair with `world_support_geom` (CONTACT_PRESENT=True on
   the real model), the dedicated test now **proves** it, and a ghost
   (both flags off) drops the pair and free-falls. **Claim holds.**

3. **Inertia matches the solid formula.** VERIFIED (pass 1, unchanged): for all
   14 labels `model.body_inertia` equals `_box_diaginertia`/`_cylinder_diaginertia`
   to float32 precision (max rel err 2.9e-8). Pass-2 sensitivity: the test catches
   a hard-coded *stale* tensor (VERIFIED FAIL) and a zero tensor (compile
   rejection, mjMINVAL). It correctly does **not** catch a `dims` change, because
   it recomputes expected from the same source of truth — that is the geometry
   mirror test's job (VERIFIED: geometry-mirror test FAILS on a drifted dims
   entry). **No stale/zero tensor; test is sensitive to its stated claim.**

4. **Support surface finite + clear of the robot.** VERIFIED. Single box
   (`mjGEOM_BOX`), half-extents `(1,1,0.05)`, top at exactly `SUPPORT_TOP_Z`
   (1.10). Centre `(2.15,0)` ⇒ x∈[1.15,3.15], y∈[−1,1], origin excluded. At the
   home state `ncon=0` for the support geom; an AABB sweep of every robot geom
   against the surface volume finds only the object resting on top. R5a holds.

5. **Geometry mirrors `scene_geometry`.** VERIFIED programmatically against the
   live `robot_moveit.scene_geometry.GEOMETRY_BY_LABEL`: same 14 labels, kinds
   and dims identical after the full→half translation. The test's transcribed
   table is itself accurate.

6. **Launch materializes the world; missing/malformed file degrades.** VERIFIED
   by driving the *real* `generate_launch_description()`: it writes
   `~/.ros/sisyphus_derived_scene.xml` containing `world_support`, `mug_1`,
   `counter_1`. Missing / malformed-JSON / wrong-shape world files each log and
   return `''`, and the fallback model has no support — no exception at
   description-build time. The bringup test's missing-file path is VERIFIED
   (fallback has no `world_support`, still has the floor).

7. **Quaternion order / extents / mass / static.** VERIFIED. `robot_skills`
   `Quaternion` is `(x,y,z,w)`; `_quat_attr` emits `(w,x,y,z)`. Box `size` =
   half the full extents; cylinder `(r, half_h)`. Free-body mass 0.3 ≫
   `mjMINVAL` (1e-15); non-graspable bodies carry no joint/inertial.

8. **Test adequacy.** See BLOCK-1 (resolved) and NOTE-1/−4/−6. Otherwise: the
   rest test is sensitive to fall-through and to a 5 mm-wrong surface (VERIFIED
   FAIL); the XML-marker test forbids the ghost markers and catches each
   single-bit case (VERIFIED); the geometry-mirror test matches the live table
   and FAILS on drift (VERIFIED); the launch test is a meaningful structural
   smoke. The inertia test catches stale/zero tensors (VERIFIED) and correctly
   leaves dims-mirroring to the mirror test. No test still passes on the broken
   code it targets.

## Out-of-scope (confirmed present, R8 — NOT this PR's blocker)

The shipped seed is physically incoherent and now visibly so: compiled and
stepped, `mug_1/plate_1/bowl_1` (z=1.15) sit *inside* the static `counter_1`
box (spans z 0.40–1.30) and are ejected upward to z≈1.33, while `book_1`/`cup_1`
(x≈0.1) are off the 2 m surface and fall to the floor (z≈0.0). Matches
implementation.md challenge #2 and R8 — a real follow-up, correctly out of PR A
scope. PR A only guarantees the *test's* seeded object rests, which it does.

## Pass-2 method note

All probes ran read-only against the installed tree: monkey-patched the test
module's `_materialize` in-process (throwaway /tmp models) to inject each
mutation, ran the **real test functions**, and restored the patch. The worktree
is left clean (`git status`: only the untracked `red_team.md`). N+1 confirmed:
after the fix, a full-file clean pass follows a clean pass (3/3), and no test
targeting a broken-code failure still passes.
