## What and why

<!-- What does this change, and why? Link the issue: Fixes #123 -->

## How it was tested

- [ ] `python -m unittest discover -s tests -t .`
- [ ] `ruff check .`
- [ ] Manual check (describe it: provider, voice or typed, what you said)

## Checklist

- [ ] A new or changed tool declares its schema, risk, default permission, category and group (`src/tools/groups.py`), and runs through `PolicyExecutor`
- [ ] Subprocesses go through `src/core/winproc.py`
- [ ] No personal data, recordings, databases or keys committed
- [ ] Docs updated (README, `docs/`, CHANGELOG `[Unreleased]`)
- [ ] Screenshots attached for UI changes
- [ ] Commits signed off (`git commit -s`)
