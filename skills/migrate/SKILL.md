# Migrate Module

Verify twin health after migration to a new machine.

## Trigger
Manual — run after moving to new hardware.

## Process
1. Verify directory structure matches twin.yaml manifest
2. Check all skill files exist and are readable
3. Verify persona/character_sheet.md is present and non-empty
4. Check wiki/index.md and wiki/log.md integrity
5. Test API connectivity (Claude API key configured)
6. Verify cron schedules are registered
7. Test source access (GitHub repos reachable)
8. Run a lightweight radar fetch to confirm web access
9. Report migration status

## Output
- Migration health report to stdout
- List of items needing manual attention
