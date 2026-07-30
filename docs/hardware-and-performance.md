# Hardware and performance notes

## Reference Fedora machine

The maintainer's intended physical acceptance machine is an HP Victus running Fedora 44 with:

- Intel Core i7-13700HX, 16 cores / 24 logical CPUs;
- approximately 15.3 GiB usable RAM;
- Intel UHD 770 graphics using the `i915` driver;
- approximately 70.07 Wh battery design capacity and 56.17 Wh last reported full capacity.

These values identify the test target; they are not benchmark results produced by Archiv.

## What the alpha records

The egress-denied acceptance summary records the operating-system/platform string, Python version, total workflow duration, run ID, and SHA-256 evidence for every retained artifact. GitHub-hosted timings demonstrate CI repeatability but must not be presented as HP Victus performance.

## Physical-machine acceptance procedure

After installing through `tools/setup-fedora.sh`, run:

```bash
mkdir -p "$HOME/archiv-alpha-evidence"
ARCHIV_HOME="$HOME/.local/share/archiv-alpha-test" \
  bash tools/run-offline-acceptance.sh \
  "$PWD" "$HOME/archiv-alpha-evidence"
```

Before publishing a performance claim, retain:

```bash
lscpu > "$HOME/archiv-alpha-evidence/lscpu.txt"
free -h > "$HOME/archiv-alpha-evidence/memory.txt"
uname -a > "$HOME/archiv-alpha-evidence/uname.txt"
```

Review `summary.json` and confirm that `status` is `succeeded`. A physical HP Victus timing is deliberately not invented in this repository; it must come from that machine's retained evidence.

## Capacity expectations

This alpha uses SQLite, local files, LibreOffice, and Poppler. It introduces no vector database or resident model requirement. Memory use is therefore dominated by document parsing/rendering and any separately configured local model. The optional model's hardware requirements are outside Archiv's deterministic core and must be documented with that model configuration.
