# Public repository policy

Making Archiv public improves review, reproducibility, discovery, and use of GitHub-hosted CI. It also creates stricter boundaries.

## Permitted repository data

- source code;
- public documentation;
- generated synthetic fixtures;
- public-domain or explicitly redistributable samples;
- redacted benchmark summaries;
- machine evidence that contains no private prompts, credentials, or source documents.

## Forbidden repository data

- personal or organisational documents;
- credentials and tokens;
- local model files;
- production databases;
- user archives and run ledgers;
- proprietary test corpora;
- unredacted logs containing paths, prompts, or document contents.

## Pull requests from forks

Fork pull requests receive no secrets and run only deterministic GitHub-hosted checks. They never execute on personal or self-hosted machines.

## Trusted heavy testing

Local-model, desktop, hardware, and strict offline tests are manual or trusted-branch workflows. Results may be published only after redaction and validation.

## Licensing

Public visibility is not itself an open-source licence. Until a licence is deliberately selected and added, reuse rights are not granted.
