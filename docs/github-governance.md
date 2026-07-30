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
- workflow-policy evidence is retained for seven days; ordinary product evidence is retained for fourteen days.

`scripts/audit_ci_trust.py` and the `CI trust boundary` workflow enforce these rules. The negative regression creates a fork-style malicious workflow containing `pull_request_target`, a secret reference, write permission, a self-hosted runner, an unpinned action, and retained checkout credentials; the auditor must reject all of them.

## Repository settings

The public API currently confirms that the repository is public, `main` is the default branch, and squash merge, merge commits, and rebase merge are all enabled. The following target state requires manual owner action in GitHub settings because workflow code cannot change repository administration safely:

| Setting | Required state |
|---|---|
| Pull request merge method | Keep squash merge enabled; disable merge commits and rebase merge |
| Head branches | Delete automatically after merge |
| `main` force pushes and deletion | Disabled |
| Required status check | Require `Fast checks / quality` after it remains stable |
| Required approving reviews | Use zero required approving reviews for the solo maintainer |
| Conversation resolution | Required before merge |
| Administrator bypass | Keep an emergency owner bypass; do not use it for routine product merges |
| Default workflow token | Read-only; workflows request narrower or explicit permissions |
| Fork pull-request workflows | GitHub-hosted only, no secrets, no write token |
| Private vulnerability reporting | Enabled |
| Dependency graph and Dependabot alerts | Enabled |
| Secret scanning and push protection | Enabled where GitHub makes them available |
| Code scanning | Enabled through the trusted-event CodeQL workflow |
| Allowed actions | GitHub-owned actions only unless a separately reviewed full-SHA exception is added |
| Merge automation | Never merge a red or incomplete product branch |

The status table above is a target configuration, not a claim that owner-only switches have already been changed. Record the date and operator in this document when each manual owner action is verified.

## Owner verification checklist

In **Settings → General**:

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
3. allow GitHub-owned actions and only deliberately reviewed exceptions.

In **Settings → Code security and analysis**:

1. enable private vulnerability reporting;
2. enable dependency graph, Dependabot alerts, and security updates;
3. enable secret scanning and push protection where available;
4. confirm CodeQL results appear after the trusted workflow runs.

## Labels and project view

Use plain-language labels: `bug`, `security`, `documentation`, `feature`, `blocked`, `needs evidence`, and `release`. A simple project view should contain `Backlog`, `Ready`, `In progress`, `Blocked`, and `Done`. These are coordination aids, never proof of technical completion.

## Incident rule

If a workflow unexpectedly receives a secret, a write token, or a self-hosted runner on untrusted code, disable that workflow first, preserve the run URL and logs, rotate exposed credentials, and reopen the public-CI hardening issue. Do not rely on deleting logs as remediation.
