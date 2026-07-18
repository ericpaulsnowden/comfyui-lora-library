# comfyui-lora-library

Keep your LoRA knowledge in plain files you own — and switch whole LoRA
setups in one click.

Two capabilities, one pack, no dependencies:

- **LoRA Notebook** — a node holding a scrollable list of entries (one per
  lora, or anything else) with an editor pane beside it. Backed by a plain
  **Markdown file you can put anywhere** — including a NAS folder shared by
  several machines. The selected entry's text is a `STRING` output you can
  wire into any prompt input. The file is the truth: edit it in ComfyUI, in
  VS Code, on the other computer — everything stays in sync.
- **LoRA Sets** — save a whole lora configuration (which loras, order,
  on/off, strengths) as a named set, then switch between sets from a
  dropdown. `Apply LoRA Set` works standalone (MODEL/CLIP in → out, plus a
  `LORA_STACK` and trigger words). If you use
  [rgthree's Power Lora Loader](https://github.com/rgthree/rgthree-comfy),
  the `LoRA Set Controller` drives it directly — capture its current rows as
  a set, apply a set back, reorder included.

> **Status: pre-release, shipping feature by feature.** Available today:
> **Apply LoRA Set** (below). The Notebook node and the rgthree Set
> Controller are in development. Contracts live in
> [docs/FORMAT.md](docs/FORMAT.md); this README describes each capability
> only once it actually ships.

## Apply LoRA Set (shipped)

`LoRA Library → Apply LoRA Set`: pick a set from the dropdown and every
enabled lora in it is applied **in the set's order** to the `model`/`clip`
you wire through. Outputs:

- `model`, `clip` — patched (or passed through untouched on `"None"`).
- `lora_stack` — a `LORA_STACK` list compatible with stack-consuming nodes
  from other packs.
- `trigger_words` — the set's stored trigger words, ready to concatenate
  into a prompt.

`strength_scale` multiplies every applied strength (quick global A/B).
Sets are JSON files in `<library folder>/sets/` — created via the API or
(soon) captured from a Power Lora Loader; hand-editing is fine too. Loras
referenced by a set resolve **separator-insensitively** with a unique-
basename fallback, so a set written on Windows applies on macOS and vice
versa; anything that can't resolve is skipped with a logged warning rather
than failing the run. After creating sets outside the graph, press `R`
(refresh node definitions) to update an open dropdown.

## Install

See [docs/INSTALL.md](docs/INSTALL.md). Short version: clone into
`ComfyUI/custom_nodes/` and restart ComfyUI. No pip requirements.

## The library folder

Everything lives in one folder — `loras.md` plus `sets/*.json` — configured
in **Settings → LoRA Library → Library folder** (server-side, so every
browser sees the same value). Point it at a shared/NAS path to use the same
library from multiple machines. Details: [docs/FORMAT.md §1](docs/FORMAT.md).

## Versioning

Backend and frontend each carry the pack version and it is shown in
**Settings → LoRA Library**; a mismatch means you pulled an update but
haven't restarted the server (or need a hard refresh). Every push bumps the
version and is tagged.

## License

MIT — see [LICENSE](LICENSE).
