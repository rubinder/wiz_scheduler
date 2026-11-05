+++
name = "copy-truth"
[triggers]
app = ["frontend/src/i18n/*.ts"]
marketing = ["src/views/**", "src/i18n/**", "src/content/**", "src/pages/**"]
+++

## Reads

The changed copy, and the app code or config at the current commit of the app clone that would back each claim.

## Checks

Every product claim (price, limits, features, integrations, legal terms) must be backed by app code or config read at the current commit of the app clone. Quote the backing file and line in the gate result. A claim with no backing is a blocking finding.

Test-only diffs (`*.test.ts` under `frontend/src/i18n/`) carry no product claims: judge them quickly as "no claims".

## Confidentiality

The repository is public. Security finding details never go into GitHub comments, issues or PRs. If you notice a security issue outside your lens, the gate result carries only "security finding — see owner notification", and the owner is notified locally.

## Pass

Every claim has a quoted backing file and line.
