# Codex Skills

A Codex-only Agent Plugins marketplace for standalone skills and reviewed compatibility packages maintained by Andrew Fai.

First-party skills link to their canonical public repositories. Reviewed third-party forks remain attributed to their upstream projects and install from immutable commits on dedicated compatibility branches. This catalogue does not duplicate source trees or include submodules. Entries are added only after the exact installation path has been tested with Codex and this repository's validator.

## Activation model

This catalog separates skill selection from permission to act:

- The behavioral overlays `quantitative-grounding`, `precise-terms`, `adhd`, and `caveman` are explicit-only. Invoke them by name; ordinary prompts do not activate them.
- `side-refresh` is also explicit-only, but it is a read-only task-context utility rather than a behavioral overlay. Invoke it through `$side-refresh`.
- `analyze-screen-feedback` is the narrow direct-intent exception. Codex may select it only when a request unambiguously asks to analyze a narrated screen recording; `$analyze-screen-feedback` remains preferred.

Selecting a skill never authorizes package installation, downloads, uploads, persistent outputs, repository writes, messages, or other side effects. Each action remains subject to its own user-intent and approval boundary.

## Marketplace installation

All six reviewed packages are pinned to full commit SHAs. Add the catalog once, then install the package you want by name:

```bash
codex plugin marketplace add andydrewie/codex-skills
codex plugin add analyze-screen-feedback@andydrewie-codex-skills
codex plugin add precise-terms@andydrewie-codex-skills
codex plugin add quantitative-grounding@andydrewie-codex-skills
codex plugin add side-refresh@andydrewie-codex-skills
codex plugin add adhd@andydrewie-codex-skills
codex plugin add caveman@andydrewie-codex-skills
```

Marketplace installation is the primary path. The selective-install commands below are retained only as compatibility and rollback records.

## Standard and Codex compatibility

Each package has one portable source of truth:

- `plugin.json` targets the published Agent Plugins 1.0.0 schema.
- `skills/<skill-name>/SKILL.md` is immediately discoverable and uses this catalog's deliberately narrow Agent Skills frontmatter profile: only `name` and `description`.
- `extensions.com.openai` contains Codex-specific plugin presentation. Per-skill `agents/openai.yaml` contains Codex skill presentation and invocation policy.
- `.codex-plugin/plugin.json` is generated compatibility output for Codex 0.145.x and earlier. It must never be edited independently. Codex 0.146.0 and later can treat the root Agent Plugins manifest as authoritative.

The bridge is currently skill-only. The generator fails closed when a package contains `mcp.json` or declares Codex apps or hooks; those capabilities will be enabled only after their schemas and portable-to-legacy behavior receive a separate security review. This prevents a compatibility file from silently changing commands, environments, server endpoints, or executable hooks.

Agent Plugins is versioned and schema-backed. Agent Skills is currently unversioned, so [`standards.lock.json`](standards.lock.json) records the exact reviewed upstream commits and document hashes. A standards monitor can flag upstream drift, but adopting a new hash remains a reviewed change.

Marketplace sources are remote Git repositories pinned to full commit SHAs. The validator checks the pinned tree and metadata without importing or executing package code. Upstream sync automation therefore cannot silently change an already published marketplace entry: a changed package must pass review and receive a new SHA.

## Validation

The tooling uses only the Python standard library and vendored Agent Plugins schemas:

```bash
# Check the marketplace structure and standards locks without network access.
python3 scripts/validate_catalog.py

# Validate a local package in place, including its generated legacy overlay.
python3 scripts/validate_catalog.py \
  --package-root plugin-name=/absolute/path/to/package

# In CI, materialize every immutable marketplace source and inspect its files.
python3 scripts/validate_catalog.py --fetch

# Regenerate after changing portable or Codex extension metadata, then check it.
python3 scripts/generate_legacy_overlay.py --write /absolute/path/to/package
python3 scripts/generate_legacy_overlay.py --check /absolute/path/to/package
```

Validation rejects mutable refs, path escapes, symlinks, nested undiscoverable skills, stale overlays, likely embedded credentials, malformed schemas, and marketplace metadata that disagrees with the pinned package. Remote validation disables Git hooks and never invokes package scripts or executables.

## Read-only drift monitors

Two GitHub workflows run every Tuesday and support manual dispatch:

- `Monitor Agent Plugin standards` runs at 04:17 UTC. It compares the published Agent Plugins versions and the unversioned Agent Skills document with `standards.lock.json`.
- `Monitor Codex skill adapters` runs at 04:41 UTC. It verifies all six immutable package tags, commits, full-tree inventories, rollback paths, marketplace pins, fork provenance, adapter invariants, upstream watched files, and the disabled Caveman fork-side sync workflow against `upstreams.lock.json`.

Both monitors use bounded GitHub REST byte reads. They never execute fetched code and never change forks, upstreams, package pins, tags, or lock files. The adapter monitor checks each tag's exact commit plus the publicly visible active ruleset, ref pattern, deletion rule, and non-fast-forward rule. GitHub hides cross-repository bypass-actor details from the workflow's least-privilege token; the release-time empty-bypass audit remains recorded in the lock, while any actual tag move is still detected directly. A reconciler keeps one issue per monitor, updates it only when the finding fingerprint changes, and closes it after a clean run.

Status and exit codes are stable: `CLEAN=0`, `REVIEW_REQUIRED=10`, `INTEGRITY_FAILURE=20`, `SOURCE_UNAVAILABLE=30`, `ALERT_FAILURE=40`, `CONFIG_OR_USAGE_ERROR=64`, and `INTERNAL_ERROR=70`. Upstream changes request review; locked-byte, pin, provenance, tag, invariant, or safety-state mismatches are integrity failures. Network or GitHub API failures do not close an existing alert.

These workflows replace the old fork-sync and duplicate local ADHD review automations only after both workflows have completed a successful manual dispatch. Pause the old automations for two weekly cycles before deleting them; do not alter the separate Matt Pocock skill update automation.

## Available skills

### analyze-screen-feedback

**Purpose:** Ground narrated screen-recording feedback in word timestamps, cursor and screen motion, readable keyframes, visible interface objects, and explicit uncertainty.

**Activation:** Narrow direct-intent exception. Codex may select it only for an unambiguous narrated screen-recording-analysis request; explicit `$analyze-screen-feedback` invocation is preferred.

**Canonical repository:** [andydrewie/analyze-screen-feedback](https://github.com/andydrewie/analyze-screen-feedback)

**Retained selective rollback installation:**

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo andydrewie/analyze-screen-feedback \
  --ref 25783c043f616c227ec077bc30c9fe671cb02297 \
  --path . \
  --name analyze-screen-feedback
```

### precise-terms

**Purpose:** Transform verbose descriptions into concise, high-signal prompts or canonical technical terms while preserving meaning and requirements.

**Canonical repository:** [andydrewie/precise-terms](https://github.com/andydrewie/precise-terms)

**Retained selective rollback installation:**

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo andydrewie/precise-terms \
  --ref b74028f6115adf35b68e040f8241f02f58f6230c \
  --path skills/precise-terms \
  --name precise-terms
```

### quantitative-grounding

**Purpose:** Add the minimum sufficient quantitative structure for scale, comparison, likelihood, economics, uncertainty, and decision relevance without false precision.

**Activation:** Explicit invocation only through `$quantitative-grounding`; requests involving numbers, estimates, or comparisons do not activate it automatically.

**Canonical repository:** [andydrewie/quantitative-grounding](https://github.com/andydrewie/quantitative-grounding)

**Retained selective rollback installation:**

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo andydrewie/quantitative-grounding \
  --ref 26d7cd48af3868f10ca7172aaaf6fd4ac9b1f1c4 \
  --path . \
  --name quantitative-grounding
```

### side-refresh

**Purpose:** Bring a side conversation up to date with the active main Codex task without navigating to, interrupting, or mutating it.

**Activation:** Explicit invocation only through `$side-refresh`; ordinary requests to summarize or check status do not activate it.

**Task and privacy boundary:** Requires Codex app task-list and task-read capabilities, treats task content as untrusted read-only evidence, minimizes sensitive details, and never messages the main task unless the user separately and explicitly requests a relay.

**Canonical repository:** [andydrewie/side-refresh](https://github.com/andydrewie/side-refresh)

**Retained selective rollback installation:**

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo andydrewie/side-refresh \
  --ref 8068ef9f235d74b7347efc3deea4eb15e4f4cd2b \
  --path . \
  --name side-refresh
```

## Verified third-party fork packages

These packages adapt upstream skills to the Codex skill schema without placing custom commits on each fork's syncable `main` branch. Install commands pin immutable commit SHAs so upstream synchronization cannot silently change installed behavior.

### adhd

**Purpose:** Generate a broad set of ideas in isolated parallel Codex sub-agents, then score, cluster, reject traps, and deepen the strongest options.

**Upstream project:** [UditAkhourii/adhd](https://github.com/UditAkhourii/adhd)

**Verified fork package:** [andydrewie/adhd `codex-plugin-v0.1.4-codex.1` at `9c473f8`](https://github.com/andydrewie/adhd/tree/9c473f80636ecc3769ee234fb332994ecb05c2dd)

**Retained rollback path:** [`24d18f8/skills/adhd`](https://github.com/andydrewie/adhd/tree/24d18f865d2378a2d1a212c48f59cc578b64189d/skills/adhd)

**Compatibility policy:** Explicit invocation only through `$adhd` or an unambiguous request for ADHD mode. The Codex adaptation uses available child-agent capacity, with up to four isolated divergent branches in one wave; it never pretends serial work is isolated. The workflow is intentionally more expensive than a direct answer and can launch up to seven child-agent calls across divergence and deepening.

**Side-effect boundary:** Spawns child agents for analysis but does not itself authorize repository writes, messages, or other external actions.

**Retained selective rollback installation (2026-08-06):**

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo andydrewie/adhd \
  --ref 24d18f865d2378a2d1a212c48f59cc578b64189d \
  --path skills/adhd \
  --name adhd
```

### caveman

**Purpose:** Compress replies into terse, low-filler language while preserving technical accuracy, safety, and required Codex progress communication.

**Upstream project:** [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman)

**Verified fork package:** [andydrewie/caveman `codex-plugin-v0.1.0-codex.1` at `6fd290f`](https://github.com/andydrewie/caveman/tree/6fd290f5ffd6f20ba82ea6fa54519deea173216e/agent-plugins/caveman)

**Retained rollback path:** [`c71d33b/codex-skills/caveman`](https://github.com/andydrewie/caveman/tree/c71d33b24b1ceb15dbfd2994adb5bcb9dd490860/codex-skills/caveman)

**Compatibility policy:** Explicit invocation only through `$caveman` or an unambiguous request to activate Caveman mode. Generic requests such as “be brief” do not activate it. Required progress notes, safety warnings, approvals, and blocker explanations remain visible and are compressed only when clarity is preserved.

**Cost note:** The upstream benchmark reports about 65% lower output across ten verbose-reply prompts, but the full rules add roughly 1,000 to 1,500 input tokens per turn. The skill can be net-negative for terse or tool-heavy work and for request-priced services; treat it primarily as a readability and output-style tool unless an A/B test shows savings for the target workload.

**Side-effect boundary:** Changes response style for the current task after activation. It does not itself authorize persistent file changes, external messages, or repository operations.

**Retained selective rollback installation (2026-08-06):**

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo andydrewie/caveman \
  --ref c71d33b24b1ceb15dbfd2994adb5bcb9dd490860 \
  --path codex-skills/caveman \
  --name caveman
```

## Inclusion policy

Future entries require a public source, license attribution, a reviewed and immutable installation ref, a validated selective-install path, explicit activation and side-effect boundaries, and honest cost or privacy notes where relevant. Canonical standalone repositories and reviewed fork packages remain the sources of truth for their respective entries.
