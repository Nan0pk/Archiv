# Privacy-safe diagnostics and issue reports

Create a support bundle only when you choose to:

```bash
archiv diagnostics-export archiv-support.json
```

Archiv displays **every field** first. Confirm to save, or answer no to leave no file.
The desktop has the same flow under **Settings → Export diagnostics…**. The JSON schema is
version `1`; support should interpret it with [diagnostics compatibility](diagnostics-compatibility.json).

The allow-listed fields are versions, sanitized platform facts, dependency presence and
versions, schema versions, database readability, aggregate ingestion states, fixed error
categories, and aggregate doctor/processing validation outcomes. Document names, paths,
identifiers, excerpts, questions, answers, prompts, model endpoints, credentials, raw
errors, configuration and environment variables are excluded. See [Security](../SECURITY.md)
for guarantees and residual fingerprinting/count-disclosure risks.

For a non-security bug, use the repository's documented **Bug report** issue template and
attach only the reviewed bundle: <https://github.com/Nan0pk/Archiv/issues/new/choose>.
Never attach an Archiv home, database, document archive, raw logs, or credentials. Report
vulnerabilities through the private route in [Security](../SECURITY.md), not a public issue.
