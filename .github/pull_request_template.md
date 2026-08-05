## Summary

Describe the problem, the change, and why this is the smallest safe solution.

## Security and compatibility

Describe effects on secrets, metadata, offline operation, verification,
failure modes, recovery, performance, and compatibility. Write `None` only
after considering each area.

## Verification

List the exact tests run and their results. Separate simulator or desktop
testing from checks performed on a physical CYD.

## Checklist

- [ ] I used only published test vectors or clearly disposable test data.
- [ ] I added or updated tests for changed behaviour.
- [ ] I updated user or design documentation where needed.
- [ ] I did not commit generated build output, caches, serial captures, or wallet material.
- [ ] I did not generate a release manifest with the dirty-tree override.
- [ ] I described physical hardware validation accurately, or stated that none was performed.
