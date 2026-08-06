# Codex Skills

A lightweight catalogue of standalone Codex skills maintained by Andrew Fai.

Each listed skill has its own canonical public repository. This catalogue links to those repositories and does not duplicate their source trees or include them as submodules. Entries are added only after the skill and its installation path have been validated.

## Available skills

### analyze-screen-feedback

Ground narrated screen-recording feedback in word timestamps, cursor and screen motion, readable keyframes, visible interface objects, and explicit uncertainty.

- **Canonical repository:** [andydrewie/analyze-screen-feedback](https://github.com/andydrewie/analyze-screen-feedback)
- **Privacy:** Local-first; recordings are not uploaded, source media is not modified, and outputs are temporary by default.
- **Platform:** macOS on Apple Silicon for the version 1 MLX transcription path.
- **Install:**

  ```bash
  python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
    --repo andydrewie/analyze-screen-feedback \
    --path . \
    --name analyze-screen-feedback
  ```

See the canonical repository for requirements, capabilities, limitations, and the pinned local model configuration.

## Future entries

Future skills will be added here only after their standalone repositories, privacy behavior, and installation instructions have been verified. Each standalone repository remains its single source of truth.
