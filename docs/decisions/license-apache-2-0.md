# Decision: Apache-2.0 for the Archiv public source

## Status

Decided for the 0.1.0a6 slice. Closes issue #2.

## Context

Archiv is public but was previously unlicensed: copyright reserved, source
available for inspection only. That state blocks contributions, downstream
packaging (including Fedora packaging of the included installer), and any
reuse, so the pre-alpha project needed an explicit licence decision.

Issue #2 asked for a comparison covering contributor friendliness, patent
protection, hosted-service obligations, compatibility with likely
dependencies, and whether future commercial distribution should stay
possible.

## Options considered

| Licence                    | Contributor friendliness | Patent protection            | Hosted-service obligations                   | Dependency compatibility                 | Commercial distribution possible    |
| -------------------------- | ------------------------ | ---------------------------- | -------------------------------------------- | ---------------------------------------- | ----------------------------------- |
| Apache-2.0                 | High; familiar, standard | Explicit patent grant        | None                                         | Compatible with all current dependencies | Yes                                 |
| MIT                        | Highest simplicity       | None (implicit at best)      | None                                         | Compatible                               | Yes                                 |
| MPL-2.0                    | Moderate                 | Explicit patent grant        | None; file-level copyleft                    | Compatible                               | Yes, with file-level copyleft terms |
| GPL-3.0-or-later           | Moderate; strong copyleft| Explicit patent grant        | None                                         | One-way friction with permissive reuse   | Constrained by copyleft             |
| AGPL-3.0-or-later          | Low for this adoption stage| Explicit patent grant      | Network use triggers source offer            | Same friction as GPL-3.0                 | Constrained; discourages adoption   |
| Source-visible proprietary | Low; no OSS reuse        | None granted                 | None                                         | Irrelevant (no reuse rights)             | Only by the copyright holder        |

Current runtime dependencies are all permissively licensed (MIT, BSD-3,
HPND/PIL, Apache-2.0) plus one LGPL-3.0 optional benchmark extra
(`python-bidi`, kept as a separate benchmark-only dependency). None of them
constrain the project's own licence; dependency notices remain separate and
are not copied as Archiv's licence.

## Decision

Archiv is licensed **Apache-2.0**.

## Rationale

- **Patent protection matters for this product.** Archiv sits in the
  knowledge-work and document-processing space where contribution disputes
  over method patents are plausible. Apache-2.0 grants users an explicit
  patent licence from contributors and includes a retaliation clause; MIT
  does not. That materially improves trust for adopters embedding Archiv
  into internal tooling.
- **Commercial distribution must remain possible.** The issue explicitly
  asks to keep that option open. Apache-2.0 permits it without negotiation.
- **Hosted-service obligations are not a fit here.** Archiv is a local-first
  executable, not a network service; there is no SaaS loophole to close, and
  AGPL's network-copyleft would mostly add adoption friction for an alpha
  project whose trust model is local inspection and verification.
- **MPL-2.0 was the strongest alternative.** Its file-level copyleft is
  attractive, but Apache-2.0 is more familiar to the Fedora/Linux packaging
  ecosystem the installer targets and matches the predominant licences of
  Archiv's own dependencies.
- **Source-visible-but-proprietary was rejected.** The repository's entire
  trust story (public fixtures, public CI evidence, public issues) depends on
  outsiders being able to inspect *and* exercise the code. Inspection without
  reuse rights leaves that story half-finished.

## Consequences

- A verbatim Apache-2.0 `LICENSE` file is added at the repository root.
- `pyproject.toml` records `license = { text = "Apache-2.0" }` for package
  metadata; the wheel ships the licence text via the default license-file
  inclusion.
- README and CONTRIBUTING state the licence and that contributions are
  accepted under the same terms, with the Apache-2.0 contributor licence
  grant applying by default text of the licence.
- Dependency licences are not redistributed as Archiv's licence; third-party
  notices stay separate.
- Files that previously said "no licence selected yet" are updated. The
  decision can be revisited before a 1.0 release, but Apache-2.0 rights
  already granted cannot be revoked retroactively.
