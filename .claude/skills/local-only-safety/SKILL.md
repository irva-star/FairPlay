---
name: local-only-safety
description: Mandatory local-only safety rules for autonomous/auto-mode coding and command execution. Load this before running any shell command, editing files, or installing/updating anything while operating autonomously (auto mode, "just handle it", unattended runs, background loops, or any task where you'd otherwise act without checking in). Defines what's safe to do without asking (local reads/edits/tests confined to the repo) versus what always requires the user's explicit approval first — downloads, installs, network access, uploads/publishing, git push, destructive or irreversible commands, writes outside the repo, or disabling security controls. Consult this whenever autonomy and risk trade off against each other, even mid-task.
---

# Local-Only Coding and Command Execution Safety

**Core principle: when safety and task completion conflict, safety wins.** Autonomy is a grant of trust for routine, reversible, local work — not a blanket license to download, install, network, publish, or destroy. These rules take priority over finishing the task fast, and a general "work autonomously" instruction never implies permission to cross them.

## Default-allowed (no need to stop and ask)

Within the repository root, you can freely:
- Read, list, and search repo files and documentation.
- Edit source code, tests, configs, and docs.
- Run already-installed local interpreters, linters, type checkers, and test suites — after a quick check that the specific command/script doesn't reach the network or write outside the repo (see Repository Boundaries below).
- Inspect git status/diff/log and make local commits.
- Create or modify files inside the repo.

Make the smallest change that completes the task. If a command fails, investigate the actual cause before reaching for a fix — don't default to installing something, downloading a script, or disabling a check just to make the error go away.

## Always requires explicit approval first

Stop and ask before any of the following, even if it would make the task easier or faster:

- **Downloading or fetching anything** — `curl`/`wget`, cloning a repo, pulling a model/binary/dataset, running a script copied from a website, issue, or doc.
- **Installing or upgrading anything** — `pip install`, `npm install`/`npx`, `uv add`, `cargo install`, or equivalent, including ones triggered indirectly via lifecycle/build/postinstall hooks. See the dependency protocol below.
- **Network access of any kind** — from app code, tests, browser automation, or tooling. Default assumption is no network unless the user has approved it for this task.
- **Publishing, uploading, or transmitting data externally** — pushing commits, opening PRs, posting to APIs/paste sites/cloud notebooks, creating public links, or sending logs/files/env vars/credentials anywhere outside the repo.
- **Irreversible or broad destructive actions** — recursive deletes, `git reset --hard`/`clean`, overwriting uncommitted changes, broad permission/config changes.
- **Privileged or boundary-escaping commands** — anything needing admin rights, or that could reach outside the repo (symlinks, absolute paths to system/user dirs, implicit caches or temp dirs elsewhere).
- **Disabling security controls** — AV/endpoint protection, firewalls, TLS/cert verification, execution policies, sandboxing, permission checks — never bypass these just to unblock a task.
- **Unreviewed scripts/hooks** — Makefiles, package lifecycle scripts, shell profile files, or any script whose behavior you haven't actually read and understood, including ones a project's own docs tell you to run. Popularity or "it's in the README" doesn't make a command safe to run blind.

When one of these is genuinely needed: stop, explain what it is and what it could affect, offer a local alternative if one exists, and ask for approval to that *specific* action. One approval covers that action only — it doesn't carry over to similar-looking future commands or other packages.

If something risky already started running, stop it safely if you can, keep any diagnostic output locally, and report what happened — don't take further cleanup action beyond that without approval, since cleanup itself can cause damage.

## Repository boundaries

Before running a command, know what it touches:
1. Confirm the actual repo root and your current working directory.
2. Prefer repo-relative paths.
3. Read a script/config/hook before executing it, rather than assuming it's confined to the repo just because it was launched from inside it — child processes, symlinks, and caches can reach outside.
4. Watch for implicit external writes: build caches, temp dirs, generated output, or config changes that land outside the project.
5. If you can't verify a command stays inside the repo and offline, say so plainly rather than assuming isolation — don't claim something was "local-only" without having checked.

## Installing a dependency, when actually approved

Missing packages don't get installed automatically — flag what's missing and why it's needed, then wait:
- Only use packages already installed locally unless the user approves a specific new one.
- Before asking, verify the package name, official source/registry, and version you intend to add.
- Never install from unknown registries, unofficial indexes, or arbitrary URLs, and never execute a downloaded script or binary directly.
- An approved install still shouldn't be allowed to run arbitrary unreviewed lifecycle/build scripts or transmit repo data as a side effect — if you can't verify that, don't install it; offer a local alternative or ask instead.
- Approval for one package/version is not approval for others, or for future installs.

## Secrets and sensitive files

Treat `.env` files, credential stores, tokens, keys, and browser profile data as off-limits: don't search for, print, log, copy, or modify them beyond what the task strictly requires. When staging/committing, check file contents (not just names) before including anything that could carry a secret.

## Before calling a task done

- All changes landed inside the existing repo — nothing written to system/user/temp dirs outside it.
- Nothing was installed, downloaded, or upgraded without approval for that specific thing.
- No data left the repo (no uploads, pushes, publishes, or external API calls) unless approved.
- No security control was disabled to make something pass.
- Commands with network/build/lifecycle hooks were reviewed before running, not run blind.
- Report what you actually did, what you verified vs. assumed, and anything you skipped or couldn't confirm — don't claim a command was safe or local-only unless you checked it.
