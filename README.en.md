<p align="center"><img src="cover.png" width="760" alt="daihuo-fanpai"></p>
<p align="center"><a href="README.md">中文</a> · <b>English</b></p>

# daihuo-fanpai · Replicate viral e-commerce short videos

Reverse-engineer a viral livestream/带货 short video → transfer it to *your* product → regenerate the shots with Jimeng/Seedance → dub & stitch into a finished cut. An **agent-driven** pipeline (built for agents like [Claude Code](https://claude.com/claude-code)); every intermediate artifact is human-reviewable and editable.

> **Core insight**: the reason "an agent replicates worse than a human" is **not the model — it's the reverse-engineering → prompt-writing handoff, which drops details and actions.** Every design choice here defends one rule: carry the subject + action + key details from the reverse-engineering *verbatim* into generation, with a completeness gate and a human review gate as backstops.

## The story: from a single question to a whole toolkit

This project wasn't architected up front — it was **forced out, step by step, by one concrete question**. Documenting it here because "why it's built this way" matters more than the code:

1. **The question**: replicating a viral带货 video by hand gets you ~60-70% there; but automate it as an agent and the result is *worse*. First instinct — "the model isn't good enough."
2. **The reversal**: digging into the failed pipeline showed it **wasn't the model**. The old orchestration cut shots by *transcript* (losing the hard-cut rhythm), bypassed the detailed reverse-engineering, and fell back to canned templates — the reverse pass *had* captured "orange herbal water, white clay mask, a hand tearing the sea cucumber open," but the prompt-writing step dropped them. **The disease is in the handoff, not the generation.**
3. **Validation** (step by step, every artifact reviewed): first prove a "reverse misses a detail → fix → regenerate" loop on one case; then validate **swapping in a new product**; then **cross-category** (borrow another category's viral structure for your own product).
4. **Landing it**: freeze each validated step into a **pluggable engine** (reverse / plan / dub / generate / assemble / judge), and add the three high-frequency judgment loops a human does but an agent tends to skip — a **completeness gate** (did the prompt keep the action?), a **human review gate** (approve the script/plan before burning credits), and a **judge** (score the cut against the methodology). Along the way, hit real potholes (AI inventing the packaging, a broken download killing the batch, the polyphonic character 参 mis-read, running out of credits, cross-category visuals that can't be mapped mechanically…) — **hit one, fix one, freeze one** — until it became a self-contained skill another agent can take over and modify.

In one line: **it's not "one prompt → one video", it's "a knowledgeable console + a playbook"** that makes the high-frequency judgments you do by hand explicit, reusable and auditable. That's also why it beats a "deterministic web-UI wrapper" — on cases it hasn't seen, it reasons, instead of falling back to canned templates.

## What it can do

| Mode | Description | Measured judge score (三看漏斗 / "three-look funnel", out of 90) |
|---|---|---|
| **A · Faithful replication** | Same script, same product, replicate as-is | 54/90 |
| **Product transfer** | Original structure + swap in your product/packaging | packaging preserved |
| **B · Cross-category** | Borrow another category's viral structure + methodology-localized script | 69/90 |

Higher score = more likely to "scale spend". Cross-category (understanding-based re-conception) scored highest — **methodology-guided re-conception > mechanical replication.**

## Pipeline

```
target video ─▶ reverse (seed_reverse) ─▶ which product photos are needed (needed_assets)
            ─▶ plan (plan_segments, with completeness gate) ─▶ [Mode B: script localization + human review]
            ─▶ dub (tts_segments, multi-speaker + polyphone fix) ─▶ generate (gen_segments, Jimeng/Volcano dual backend)
            ─▶ assemble ─▶ judge (score against the "three-look funnel")
```

Each step hands off only via JSON files/folders — **pluggable**: swapping the reverse VLM, the video model, or the TTS only touches one script (see `DESIGN.md`).

## Engine scripts

| Script | Role |
|---|---|
| `doctor.py` | Environment preflight (tiered deps: light→auto-install / credentials & heavyweight→prompt user) |
| `seed_reverse.py` | Reverse: native video → structured shot-list JSON (hard cuts / transcript / product_role / key colors) |
| `needed_assets.py` | After reverse, list which product-form photos this video needs |
| `plan_segments.py` | Segment / route / write prompts (carry the full action) + completeness gate |
| `localize_seed.py` | Mode-B script localization (drafted by Seed 2.1 Pro fed with the methodology pack) |
| `localize_apply.py` | Write the revised lines back into the shot-list |
| `tts_segments.py` | Dubbing (multi-speaker A/B + polyphonic-character pronunciation fix) |
| `gen_segments.py` | Generate (Jimeng CLI + Volcano Ark dual backend, `--i2v-backend ark`) |
| `ark_gen.py` | Volcano Ark Seedance 2.0 backend (token-billed, saves the CLI credit pool) |
| `assemble.py` | Normalize + concat + lay a continuous voiceover track |
| `judge.py` | Judge: score against the "three-look funnel" (out of 90) + concrete fixes |

## Dependencies & config

- **Jimeng (Dreamina) CLI**: video generation (needs maestro VIP). `curl -fsSL https://jimeng.jianying.com/cli | bash`, then `dreamina login`.
- **Volcano Ark**: reverse (Seed 2.1 Pro) + optional video generation (Seedance 2.0). Set `export ARK_API_KEY=your_key` (or write it to `~/.config/daihuo-fanpai/ark_key`).
- **CosyVoice**: local TTS (optional; degrades gracefully if missing). Location via `export COSYVOICE_HOME=/path/to/CosyVoice`, defaults to `~/CosyVoice`.
- **ffmpeg** + Python (`requests`, optionally `google-genai`).

> **Config lives in `config.py`** — all keys/paths come from environment variables, **no hardcoded secrets**. Run `python3 doctor.py` first; it tells you what's missing and whether it can be auto-fixed, by dependency tier.

## Methodology pack (not included)

Mode-B script localization relies on a `qianchuan/` methodology pack (topic selection / sentence patterns / cross-category copy / diagnostic rubric / compliance red-lines). **This repo does not include it** (distilled from paid courses). You can either bring your own methodology into `qianchuan/` following the `QC_FILES` convention in `localize_seed.py`, or skip `localize_seed.py` and write the script yourself (Mode A doesn't need it at all).

## Usage (brief)

```bash
python3 doctor.py                                   # 0 preflight
python3 seed_reverse.py target.mp4 --out run/shotlist.json
python3 needed_assets.py run/shotlist.json          # which product photos → fill assets.json
python3 plan_segments.py run/shotlist.json assets.json --out run/segments.json  # review run/segments.md
python3 tts_segments.py run/segments.json --out-dir run/audio/seg
python3 gen_segments.py run/segments.json --clips run/clips --audio-dir run/audio/seg [--i2v-backend ark]
python3 assemble.py run/segments.json --clips run/clips --audio-dir run/audio/seg --out run/output/FULL.mp4
python3 judge.py run/output/FULL.mp4
```

See **`SKILL.md`** (how an agent uses it), **`DESIGN.md`** (design rationale + data contracts + extension points), **`HANDOFF.md`** (porting/handoff).

## Notes

- The output is a **rough cut**: on-screen sales text, precise captions, frame-level trimming go to post-editing (don't over-ask the generation model).
- Billing: Jimeng CLI uses a monthly credit pool; Volcano Ark bills per token (use `--i2v-backend ark` to offload i2v shots and save credits).
- **Compliance is on you**: no medical/efficacy claims for food, no fabricated celebrity endorsements, promo/price must be truthful.

## License

MIT · Copyright (c) 2026 wangcanyu
