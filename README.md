# Codex Skills

A lightweight catalogue of standalone Codex skills and reviewed compatibility packages maintained by Andrew Fai.

First-party skills link to their canonical public repositories. Reviewed third-party forks remain attributed to their upstream projects and install from immutable commits on dedicated compatibility branches. This catalogue does not duplicate source trees or include submodules. Entries are added only after the exact selective-install path has been tested with the official Codex installer and validator.

## Available skills

### analyze-screen-feedback

**Purpose:** Ground narrated screen-recording feedback in word timestamps, cursor and screen motion, readable keyframes, visible interface objects, and explicit uncertainty.

**Canonical repository:** [andydrewie/analyze-screen-feedback](https://github.com/andydrewie/analyze-screen-feedback)

**Verified selective installation:**

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo andydrewie/analyze-screen-feedback \
  --path . \
  --name analyze-screen-feedback
```

### precise-terms

**Purpose:** Transform verbose descriptions into concise, high-signal prompts or canonical technical terms while preserving meaning and requirements.

**Canonical repository:** [andydrewie/precise-terms](https://github.com/andydrewie/precise-terms)

**Verified selective installation:**

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo andydrewie/precise-terms \
  --path skills/precise-terms \
  --name precise-terms
```

### quantitative-grounding

**Purpose:** Add the minimum sufficient quantitative structure for scale, comparison, likelihood, economics, uncertainty, and decision relevance without false precision.

**Canonical repository:** [andydrewie/quantitative-grounding](https://github.com/andydrewie/quantitative-grounding)

**Verified selective installation:**

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo andydrewie/quantitative-grounding \
  --path . \
  --name quantitative-grounding
```

## Verified third-party fork packages

These packages adapt upstream skills to the Codex skill schema without placing custom commits on each fork's syncable `main` branch. Install commands pin immutable commit SHAs so upstream synchronization cannot silently change installed behavior.

### adhd

**Purpose:** Generate a broad set of ideas in isolated parallel Codex sub-agents, then score, cluster, reject traps, and deepen the strongest options.

**Upstream project:** [UditAkhourii/adhd](https://github.com/UditAkhourii/adhd)

**Verified fork package:** [andydrewie/adhd at `24d18f8`](https://github.com/andydrewie/adhd/tree/24d18f865d2378a2d1a212c48f59cc578b64189d/skills/adhd)

**Compatibility policy:** Explicit invocation only through `$adhd` or an unambiguous request for ADHD mode. The Codex adaptation uses available child-agent capacity, with up to four isolated divergent branches in one wave; it never pretends serial work is isolated. The workflow is intentionally more expensive than a direct answer and can launch up to seven child-agent calls across divergence and deepening.

**Side-effect boundary:** Spawns child agents for analysis but does not itself authorize repository writes, messages, or other external actions.

**Verified selective installation (2026-08-06):**

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

**Verified fork package:** [andydrewie/caveman at `c71d33b`](https://github.com/andydrewie/caveman/tree/c71d33b24b1ceb15dbfd2994adb5bcb9dd490860/codex-skills/caveman)

**Compatibility policy:** Explicit invocation only through `$caveman` or an unambiguous request to activate Caveman mode. Generic requests such as “be brief” do not activate it. Required progress notes, safety warnings, approvals, and blocker explanations remain visible and are compressed only when clarity is preserved.

**Cost note:** The upstream benchmark reports about 65% lower output across ten verbose-reply prompts, but the full rules add roughly 1,000 to 1,500 input tokens per turn. The skill can be net-negative for terse or tool-heavy work and for request-priced services; treat it primarily as a readability and output-style tool unless an A/B test shows savings for the target workload.

**Side-effect boundary:** Changes response style for the current task after activation. It does not itself authorize persistent file changes, external messages, or repository operations.

**Verified selective installation (2026-08-06):**

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo andydrewie/caveman \
  --ref c71d33b24b1ceb15dbfd2994adb5bcb9dd490860 \
  --path codex-skills/caveman \
  --name caveman
```

## Inclusion policy

Future entries require a public source, license attribution, a reviewed and immutable installation ref, a validated selective-install path, explicit activation and side-effect boundaries, and honest cost or privacy notes where relevant. Canonical standalone repositories and reviewed fork packages remain the sources of truth for their respective entries.
