# Repository rulesets

`protect-main.json` is the repository copy of the active **Protect main**
ruleset. GitHub does not apply this file automatically. To restore or audit the
hosted rule:

1. Open **Settings** > **Rules** > **Rulesets**.
2. Select **New ruleset** > **Import a ruleset**.
3. Select `protect-main.json` and review the imported settings.
4. Confirm that enforcement is **Active** and the target is the default branch.
5. Create a test pull request and confirm that all four status checks are
   required before merging.

The ruleset blocks deletion and force pushes and requires squash-merged pull
requests, linear history, resolved conversations, and successful Linux,
Windows, Python 3.10 and Python 3.12 checks. It requires zero approvals while
there is one maintainer. Increase the approval count when independent reviewer
coverage is available. Commit signing is not required; enable it only after
every maintainer has configured GitHub-verified signing.
