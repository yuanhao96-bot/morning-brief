# Persona Module

Build and maintain the user's character sheet from their corpus.

## Trigger
Manual — run after syncing new transcripts or writing samples.

## Inputs
- `sources/corpus/` — Claude Code transcripts, writing samples, git logs
- `persona/character_sheet.md` — current persona (if exists)

## Process
1. Read all new/updated files in `sources/corpus/`
2. Extract behavioral signals:
   - Communication style (tone, verbosity, formality)
   - Reasoning patterns (how they approach problems)
   - Domain expertise levels and interests
   - Values and decision heuristics
   - Known biases and blind spots
3. Update `persona/character_sheet.md` with new observations
4. Update relevant wiki pages in `wiki/persona/`
5. Update `wiki/index.md` under `## Persona`:
   ```markdown
   - [[persona/communication-style]] — one-line description
   ```
   Keep entries sorted alphabetically.
6. Log changes to `wiki/log.md`

## Output
- `persona/character_sheet.md` — structured persona document
- `wiki/persona/*.md` — detailed persona knowledge pages
