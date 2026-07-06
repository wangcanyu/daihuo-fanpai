<p align="center"><img src="cover.png" width="760" alt="daihuo-fanpai"></p>
<p align="center"><a href="README.md">中文</a> · <b>English</b></p>

# daihuo-fanpai · Viral E-commerce Short-Video Replication Skill

Reverse-engineer a viral product video → migrate it to *your* product → regenerate shots with Jimeng/Seedance → dub, assemble, and score a ready-to-cut draft.
An **agent-driven** pipeline (built for [Claude Code](https://claude.com/claude-code) and similar agents) where every stage emits a human-reviewable file.

> **Core insight**: when agents replicate worse than humans, the culprit is **not the video model — it's the "analysis → prompt" hand-off that drops details and actions** ("orange herbal water", "a hand tearing the sea cucumber open" get summarized away). Every design decision here defends one rule: **carry the analyzed subject + action + key details verbatim into generation**, backed by a completeness gate, a human-review checkpoint, and an AI judge.

## Modes

| Mode | What it does | Judge score (90-pt rubric) |
|---|---|---|
| **A — Faithful replica** | Same script, same product | 54/90 |
| **Product migration** | Same structure, your product/packaging | text-faithful packaging |
| **B — Cross-category** | Borrow a viral structure from another category + methodology-driven script rewrite | 69/90 |

Higher = more likely to scale in paid traffic. Understanding-driven reconstruction beats mechanical copying.

## Pipeline

```
0 doctor → 1 seed_reverse → 1.5 needed_assets (which product photos to prepare)
→ 2 plan_segments (completeness gate + human review of segments.md)
→ [Mode B: localize_seed / localize_apply + review]
→ 3 tts_segments (multi-speaker + pronunciation fixes)
→ 4 gen_segments (Jimeng + Volcano Ark dual backend; host-shot duration auto-fits dub length)
→ 5 assemble → 6 judge (uploads final AND source video for a real fidelity comparison)
→ 7 export_subs (SRT + on-screen-text checklist for your editor)
```

Stages hand off only through JSON files/folders — **pluggable**: swap the VLM, the video model, or the TTS by rewriting one script (contracts in `DESIGN.md`).

## Configuration (`config.py`, all env-overridable, no hardcoded secrets)

| Item | Purpose | Setup |
|---|---|---|
| Jimeng Dreamina CLI | video generation (lip-sync requires it; maestro VIP) | `curl -fsSL https://jimeng.jianying.com/cli \| bash`, then `dreamina login` |
| Volcano Ark | analysis/judge (Seed 2.1 Pro) + optional generation (Seedance 2.0) | `export ARK_API_KEY=...`; model via `ARK_SEED_MODEL` |
| CosyVoice (optional) | local dubbing; graceful fallbacks if absent | `export COSYVOICE_HOME=...` |
| ffmpeg + Python `requests` | cutting / assembly / HTTP | — |

> Everything talks to mainland-China endpoints directly — **no proxy needed**. Set `DAIHUO_DOWNLOAD_PROXY` only if CDN downloads fail on your network.

### `assets.json`: the product profile (switch categories without touching code)

```jsonc
{"host_anchor": "assets/host.jpg",
 "host_desc": "shoulder-length black hair, cream knit top",   // pinned into every host shot → no wardrobe drift
 "product_desc": "XX sea cucumber, navy-gold packaging",
 "products": {"hero": "...", "giftbox": "...", "innerbox": "..."},
 "forms": {"hero": ["serum","dropper"], "bottle": ["glass bottle","pump"]},  // form aliases per category
 "product_verbs": ["apply","dab","spray"]}                    // verbs the completeness gate checks
```

## Methodology ammo pack (not included)

Mode B script rewriting expects a `qianchuan/` methodology pack (topic selection / sentence patterns / cross-category SOP / scoring rubric / compliance red lines), distilled from paid courses — **not in this repo**. Bring your own per the `QC_FILES` convention in `localize_seed.py`; Mode A never needs it.

## Notes

- Output is a **rough cut**: overlays and fine subtitles belong to post-editing (`export_subs.py` prepares both).
- Billing: Jimeng CLI uses a monthly credit pool; Volcano Ark bills per token (`--i2v-backend ark` offloads i2v shots).
- **Compliance is on you**: no medical claims, no fabricated endorsements, real prices only.

## License

MIT · Copyright (c) 2026 wangcanyu
