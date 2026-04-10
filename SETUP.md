# Digital Twin — Setup Guide

Fresh macOS setup from scratch. Run these phases in order. Takes
~30 minutes if Syncthing is already in place on your main machine,
longer if you're setting Syncthing up for the first time.

The end state: a machine that runs the `radar → ingest → digest`
chain every morning at 7am, drops a daily brief into Obsidian via
Syncthing, and surfaces logs through `cron.log`.

## Phase 1: Base System

### 1.1 macOS basics
After the initial macOS setup wizard:

- System Settings → General → Sharing → **Remote Login** → on
  (so you can SSH in for debugging from your main machine)
- System Settings → Energy → **Prevent automatic sleeping** while
  on power (otherwise launchd won't fire the morning job)

```bash
# Note the machine's address for later:
ifconfig | grep "inet " | grep -v 127.0.0.1
hostname
```

### 1.2 Xcode command line tools
```bash
xcode-select --install
```

### 1.3 Homebrew
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

### 1.4 Essential tools
```bash
brew install git gh ripgrep fd node
brew install --cask syncthing obsidian
```

## Phase 2: GitHub

```bash
gh auth login
mkdir -p ~/projects && cd ~/projects
gh repo clone <your-username>/digital-twin
cd digital-twin
```

## Phase 3: Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

Configure your API key — either an environment variable in
`~/.zshrc` or via `claude /login` if you prefer the interactive
flow:

```bash
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc
source ~/.zshrc
claude --version
```

## Phase 4: Syncthing

Three folders move data between your main machine and the twin.
The directions matter — get them wrong and either side will revert
the other's writes.

| Folder on disk                  | Direction          | Folder type on twin |
|---------------------------------|--------------------|---------------------|
| `sources/corpus/`               | bidirectional      | Send & Receive      |
| `sources/sync/`                 | user → twin        | Receive Only        |
| `wiki/`                         | twin → user        | Send Only           |

Bidirectional on `sources/corpus/` is **load-bearing** — radar
writes new articles into `sources/corpus/reading/radar/` on the
twin side, and those drops need to flow back to your user machine
so you can browse them in Obsidian. If `sources/corpus/` is set to
Receive Only on the twin, Syncthing will revert radar's writes on
the next sync and the digest will mysteriously have nothing to say.

### 4.1 Install and pair
```bash
# On both machines (if not already):
brew install --cask syncthing
open -a Syncthing
```

In each Syncthing web UI (http://127.0.0.1:8384):
1. Add the other machine as a remote device (paste its device ID).
2. Add each of the three folders above. Set the correct folder type
   on the twin side per the table.
3. Wait for initial sync to complete.

### 4.2 Verify the corpus path exists on the twin
```bash
ls ~/projects/digital-twin/sources/corpus/reading/
```

If `sources/corpus/reading/radar/` doesn't exist yet, that's fine —
radar will create it on its first run.

## Phase 5: Build initial persona

The persona (`persona/character_sheet.md`) is the relevance gate
for radar and the framing voice for digest. Without it, radar
filters everything and digest sounds generic.

If you already have a `character_sheet.md` from a prior install,
Syncthing will have brought it across already. Otherwise, build
one on the twin from your corpus:

```bash
cd ~/projects/digital-twin
claude
> Run the persona skill: read all files in sources/corpus/, extract
  behavioral signals, and populate persona/character_sheet.md.
```

Review the result. Iterate until it feels accurate. This is the
foundation everything downstream rests on, so it's worth the
half-hour.

## Phase 6: Configure radar sources

Edit `skills/radar/sources.yaml` to list the URLs and queries you
want radar to scan. Each entry needs:

- `name` — human-readable label
- `category` — directory under `sources/corpus/reading/radar/` where
  drops will land (`ml`, `trading`, `biology`, etc.)
- `method` — `web_fetch` for direct URLs, `web_search` for queries
- `url` or `query` — the target
- `frequency` — `daily` or `weekly` (advisory; radar doesn't enforce
  it yet, but useful for documentation)

The default file has one example source. Replace with your own.

## Phase 7: Schedule the morning chain

The twin uses launchd, not crontab. Only **one** plist is needed
because radar execs into ingest and ingest execs into digest, so
launchd's tracked PID flows through the whole chain.

### 7.1 Create the launch agent
Save the following to
`~/Library/LaunchAgents/com.<your-username>.digitaltwin.radar.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.<your-username>.digitaltwin.radar</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/<your-username>/projects/digital-twin/run-module.sh</string>
        <string>radar</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>7</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>WorkingDirectory</key>
    <string>/Users/<your-username>/projects/digital-twin</string>

    <key>StandardOutPath</key>
    <string>/Users/<your-username>/projects/digital-twin/cron.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/<your-username>/projects/digital-twin/cron.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

### 7.2 Load it
```bash
launchctl load ~/Library/LaunchAgents/com.<your-username>.digitaltwin.radar.plist
launchctl list | grep digitaltwin
```

### 7.3 Test the chain manually
Don't wait until the morning to discover the chain is broken:

```bash
cd ~/projects/digital-twin
./run-module.sh radar
tail -50 cron.log
```

You should see five log markers:
```
... | Starting radar
... | Finished radar
... | Chaining radar → ingest
... | Starting ingest
... | Finished ingest
... | Chaining ingest → digest
... | Starting digest
... | Finished digest
```

After it finishes, `wiki/digests/$(date +%Y-%m-%d).md` should exist.
If digest writes "nothing new today" on the first run, that's fine
— it means radar didn't find anything above the relevance gate, or
ingest had nothing pending.

## Phase 8: Obsidian

```bash
open -a Obsidian
```

In Obsidian: "Open folder as vault" → select `~/projects/digital-twin/wiki/`.

Bookmark `wiki/digests/` so the morning brief is one tap away.

## Phase 9: Verify migration (optional)

If you're moving the twin from one machine to another rather than
setting it up fresh, run the migrate skill to sanity-check that
everything's in place:

```bash
cd ~/projects/digital-twin
claude
> Run the migrate skill.
```

## Quick reference

| Task                          | Command                                         |
|-------------------------------|-------------------------------------------------|
| Chat with the twin            | `cd ~/projects/digital-twin && claude`         |
| Manual full-chain run         | `./run-module.sh radar`                         |
| Manual ingest only            | `./run-module.sh ingest`                        |
| Manual digest only            | `./run-module.sh digest`                        |
| Check today's brief           | `open wiki/digests/$(date +%Y-%m-%d).md`        |
| Tail the run log              | `tail -50 cron.log`                             |
| Reload the launch agent       | `launchctl unload <plist> && launchctl load <plist>` |
| Update twin code              | `git pull`                                      |

## Troubleshooting

**Radar runs but digest is empty.** Check `cron.log` for the chain
markers. If you see "Finished radar" but no "Chaining radar →
ingest", the chain block in `run-module.sh` may be missing —
`git pull` to update. If the chain ran but digest wrote "nothing
new today", radar found nothing above the relevance gate that day,
which is normal on slow news days.

**Radar drops files but they vanish.** Almost certainly Syncthing
is reverting them because `sources/corpus/` is set to Receive Only
on the twin. Open Syncthing, change the folder type to
**Send & Receive**, restart Syncthing.

**launchd fires at the wrong hour.** Verify your system timezone
with `sudo systemsetup -gettimezone`. Set it explicitly if needed:
`sudo systemsetup -settimezone <Your/Timezone>`, then reload the
plist.

**Permission denied or scoped-tools error.** `run-module.sh` passes
a scoped tool allow-list to the headless `claude --print` invocation
(see the `case "$MODULE"` block). If a skill needs a tool that isn't
in the list, add it there — don't disable the allow-list.
