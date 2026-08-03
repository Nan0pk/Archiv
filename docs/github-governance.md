# GitHub governance and CI trust boundary

Archiv is public, pre-alpha, and maintained by one primary owner. Repository controls must protect `main` without creating an approval rule that the solo maintainer cannot satisfy.

## Effective code-enforced controls

These controls live in the repository and are tested on every relevant pull request:

- pull requests run only on GitHub-hosted runners;
- pull-request workflows declare read-only permissions and do not reference repository secrets;
- `pull_request_target` is forbidden for workflows that can execute repository code;
- every external action is pinned to a full immutable commit SHA;
- `actions/checkout` uses `persist-credentials: false`;
- superseded pull-request runs use `cancel-in-progress: true`;
- dependency changes receive GitHub's vulnerability review at `high` severity or above;
- CodeQL runs only on trusted `main`, schedule, or manual events because it needs `security-events: write`;
- workflow-policy evidence is retained for seven days; ordinary product evidence is retained for fourteen days;
- temporary external-evidence workflows must pin every source, publish only sanitized non-source evidence, delete external trees before upload, and be removed from the final pull-request diff.

`scripts/audit_ci_trust.py` and the `CI trust boundary` workflow enforce these rules. The negative regression creates a fork-style malicious workflow containing `pull_request_target`, a secret reference, write permission, a self-hosted runner, an unpinned action, and retained checkout credentials; the auditor must reject all of them.

## Repository settings verification

The repository settings below were reviewed on 2026-07-30. Evidence is classified honestly: **API/workflow verified** means GitHub returned machine-readable proof; **owner UI** means the setting is visible only in repository administration and still requires manual owner action.

| Setting | Required state | Verification |
|---|---|---|
| Pull request merge method | Squash only | **API/workflow verified**: squash enabled; merge commits and rebase disabled |
| Head branches | Delete automatically after merge | **API/workflow verified**: the PR #25 head branch was absent after merge |
| Dependency graph and dependency review | Enabled | **API/workflow verified**: GitHub dependency review rerun `30569623238` succeeded after the graph was enabled |
| Dependabot updates | Enabled | **API/workflow verified**: Dependabot opened and merged PR #27 |
| Required status check | Require `Fast checks / quality` | Verified by a governance PR attempting merge before checks complete; record the result in issue #10 |
| `main` force pushes and deletion | Disabled | Owner UI verification required |
| Pull request requirement | Required before product changes reach `main` | Owner UI verification required |
| Required approving reviews | Zero, for solo-maintainer safety | Owner UI verification required |
| Conversation resolution | Required before merge | Owner UI verification required |
| Administrator bypass | Emergency-only, not routine product merging | Owner UI verification required |
| Default workflow token | Read-only | Owner UI verification required; workflow files also declare least privilege |
| Actions approving pull requests | Disabled | Owner UI verification required |
| Allowed actions | GitHub-owned actions only, with full-SHA pinning | Owner UI verification required; repository auditor independently enforces full SHAs |
| Private vulnerability reporting | Enabled | Owner UI verification required |
| Secret scanning and push protection | Enabled where GitHub makes them available | Owner UI verification required |
| Code scanning | Trusted-event CodeQL workflow enabled | Workflow file verified; Security-tab result requires owner UI verification |
| Labels and project view | Plain-language labels and simple workflow columns | Owner UI verification required |

## Owner verification checklist

In **Settings → General → Pull Requests**:

1. keep squash merge enabled;
2. disable merge commits and rebase merge;
3. enable automatic head-branch deletion.

In **Settings → Rules → Rulesets** or branch protection for `main`:

1. require a pull request before merging;
2. require `Fast checks / quality`;
3. block force pushes and branch deletion;
4. require conversation resolution;
5. set zero required approving reviews so solo maintenance remains possible.

In **Settings → Actions → General**:

1. set default workflow permissions to read-only;
2. prevent Actions from approving pull requests;
3. allow GitHub-owned actions and only deliberately reviewed exceptions;
4. require actions to be pinned to a full commit SHA where GitHub offers that policy.

In **Settings → Security → Advanced Security**:

1. enable private vulnerability reporting;
2. enable dependency graph, Dependabot alerts, and security updates;
3. enable secret scanning and push protection where available;
4. confirm CodeQL results appear after the trusted workflow runs.

## Labels and project view

Use plain-language labels: `bug`, `security`, `documentation`, `feature`, `blocked`, `needs evidence`, and `release`. A simple project view should contain `Backlog`, `Ready`, `In progress`, `Blocked`, and `Done`. These are coordination aids, never proof of technical completion.

## Incident rule

If a workflow unexpectedly receives a secret, a write token, or a self-hosted runner on untrusted code, disable that workflow first, preserve the run URL and logs, rotate exposed credentials, and reopen the public-CI hardening issue. Do not rely on deleting logs as remediation.
