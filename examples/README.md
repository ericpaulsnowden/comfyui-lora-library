# Example workflows

## eps-full-pipeline.json

Every EPSNodes capability stitched into one complex workflow — drag the file
onto the ComfyUI canvas (or File → Open) to load it. Built and round-trip
verified against ComfyUI v0.28 / frontend 1.45; requires this pack (v0.30.0+)
and, for the state-controller corner only,
[rgthree-comfy](https://github.com/rgthree/rgthree-comfy).

The graph, stage by stage (numbered groups + notes on the canvas):

1. **Sources & Switcher** — two Load Image nodes + an EPS Frame Saver (video
   frame) feed an EPS Switcher: toggle rows to pick which sources flow;
   toggled-off branches never execute.
2. **Normalize & Collect** — EPS Resolution fits everything to 1024, then an
   EPS Image Grid records the stream (Collect) across runs; flip it to Emit
   to fan the whole collection out.
3. **Prompts & Pairs** — an EPS Prompt Notebook (multi-select) and an EPS
   Cross Product pair EVERY grid image with EVERY selected prompt; entry
   names ride along.
4. **Loras & Sweep** — Checkpoint → EPS Apply LoRA Set (reads a saved state)
   → EPS LoRA Sweep (0.0–1.0 @ 0.5 to start = 3 steps). The controller
   corner (EPS Lora Loader State Controller + Power Lora Loader) is where
   states get captured/renamed.
5. **Cross Sweep & Generate** — EPS Cross Sweep multiplies sweep steps ×
   pairs (strength-major), then img2img sampling (fixed seed 42, denoise
   0.6) saves via `save_prefix` into
   `output/eps_demo/<lora>_<strength>/<PromptName>_*.png` — one folder per
   strength.

Before running: pick a checkpoint, images, and a video path; select 2+
prompts in the Notebook; pick (or capture) a lora state. Run count =
loras × strengths × grid images × prompts — start small.
