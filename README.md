# Codex Skills

A lightweight catalogue of standalone Codex skills maintained by Andrew Fai.

Each listed skill has its own canonical public repository. This catalogue links to those repositories and does not duplicate their source trees or include them as submodules. Entries are added only after the skill and its installation path have been validated.

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

## Future entries

Future skills will be added here only after their standalone repositories, privacy behavior, and installation instructions have been verified. Each standalone repository remains its single source of truth.
