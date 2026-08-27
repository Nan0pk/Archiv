# Security maintenance and releases

## Private reports and response targets

GitHub private vulnerability reporting is the preferred channel; maintainers verify it is
enabled before every release. Do not put exploit details in public issues. A maintainer
acknowledges a report within **2 business days**, completes initial severity triage within
**5 business days**, and provides coordinated status updates at least weekly.

Critical issues target a mitigation or release within **7 calendar days**, high within
**14 days**, medium within **30 days**, and low within **90 days**. Exploited vulnerabilities
override this schedule. If a deadline cannot be met, disable the affected surface, document
the revised date privately, and notify affected users without disclosing exploit details.

## Dependency triage

Dependabot, dependency review, CodeQL, locked direct dependencies, and release-time SBOM
review provide signals. Maintainers confirm reachability and affected versions, assess
malicious-package and maintainer-compromise indicators, test the smallest safe update, and
record the advisory, decision, owner, deadline, and validation evidence. A scanner's
absence of findings is not proof of safety.

## Signed release procedure

1. Release only from a protected tag matching the reviewed commit; require passing CI and
   the stable-gate review when applicable.
2. Build in GitHub Actions with pinned actions and no untrusted pull-request code.
3. Generate wheels, source archive, SHA-256 checksums, and a CycloneDX SBOM.
4. Produce GitHub artifact attestations/SLSA provenance and sign artifacts keylessly with
   Sigstore; sign the Git tag with a maintainer identity.
5. Verify signatures, identity/issuer, provenance subject digests, and checksums in a clean
   environment before publishing. Retain logs and attestations with the release.
6. For compromise, stop distribution, revoke/rotate credentials, publish corrected signed
   artifacts and an advisory, and never silently replace an existing release asset.

The repository workflow implements provenance and attestation scaffolding; maintainers are
responsible for protected environments and signing identities in repository settings.
