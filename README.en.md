<p align="center"><img src="cover.png" width="760" alt="daihuo-fanpai"></p>
<p align="center"><a href="README.md">中文</a> · <b>English</b></p>

# daihuo-fanpai · Viral E-commerce Short-Video Replication Skill

Reverse-engineer a viral product video → migrate it to *your* product → regenerate shots with Jimeng/Seedance → dub, assemble, score → **deliver a ready-to-edit JianYing (CapCut CN) draft or a subtitled final cut**.
An **agent-driven** pipeline (built for [Claude Code](https://claude.com/claude-code) and similar agents) where every stage emits a human-reviewable file.

> **Core insight**: when agents replicate worse than humans, the culprit is **not the video model — it's the "analysis → prompt" hand-off that drops details and actions** ("orange herbal water", "a hand tearing the sea cucumber open" get summarized away). Every design decision here defends one rule: **carry the analyzed subject + action + key details verbatim into generation**, backed by a completeness gate, a human-review checkpoint, and an AI judge.

## Modes

| Mode | What it does | Judge score (90-pt rubric) |
|---|---|---|
| **A — Faithful replica** | Same script, same product | 54/90 |
| **Product migration** | Same structure, your product/packaging | text-faithful packaging |
| **B — Cross-category** | Borrow a viral structure from another category + methodology-driven script rewrite | 69/90 |
| **A — Multi-character skit** | 3-character office-comedy replica (multi-anchor cast + original-audio lip sync) | fidelity 63/100 |
| **A — Batch production** | Whole-catalog replication of one product, fully automated SOP | 16 deliveries in one day, 40-60 min each at cruise |

Higher = more likely to scale in paid traffic. Understanding-driven reconstruction beats mechanical copying. Story-driven originals score low on the ads rubric by nature — judge them by fidelity instead.

## Pipeline

```
0 doctor → 1 seed_reverse → [1b dual reverse: k3_reverse + merge_reverse evidence adjudication]
→ 1.5 needed_assets (which product photos to prepare)
→ 2 plan_segments (completeness gate + human review) → [skits: patch_cast anchors + hard cast-count constraint]
→ [Mode B: localize_seed / localize_apply + review]
→ 3 tts_segments (multi-speaker + pronunciation fixes | audio reuse: cut_audio with 2-second upload gate)
→ 4 gen_segments (Jimeng / Volcano / Xiaoyunque backends; auto retry on moderation & network flaps;
                  post-download decode check catches corrupted streams)
→ 5 assemble → [skits: qc_lipsync frame-level lip QC → redraw dirty segments]
→ 6 judge (uploads final AND source video for a real fidelity comparison)
→ 7 export_subs (SRT + on-screen-text checklist) → 8 deliver (★JianYing draft | burned-subtitle final)
```

Stages hand off only through JSON files/folders — **pluggable**: swap the VLM, the video model, or the TTS by rewriting one script (contracts in `DESIGN.md`).

## Configuration (`config.py`, all env-overridable, no hardcoded secrets)

| Item | Purpose | Setup |
|---|---|---|
| Jimeng Dreamina CLI | video generation (lip-sync requires it; maestro VIP) | `curl -fsSL https://jimeng.jianying.com/cli \| bash`, then `dreamina login` |
| Volcano Ark | analysis/judge (Seed 2.1 Pro) + optional generation (Seedance 2.0) | `export ARK_API_KEY=...`; model via `ARK_SEED_MODEL` |
| CosyVoice (optional) | local dubbing; graceful fallbacks if absent | `export COSYVOICE_HOME=...` |
| pyJianYingDraft (optional) | JianYing draft delivery; falls back to `--mode final` | own venv; drafts dir via `DAIHUO_JY_DRAFTS` |
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

## Two delivery shapes (`deliver.py`)

- **JianYing draft (recommended)**: the pipeline's segment structure flows straight into the editor — video track laid segment by segment (boundaries = cut points), dub track aligned, per-sentence editable subtitles, the source video's on-screen text placed as a reference track, plus an empty BGM track. Media is copied into the draft folder (self-contained). Verified on JianYing 10.7: it encrypts drafts *on save* but happily *reads* plaintext drafts; if a future version breaks this, fall back to `final`.
- **Burned-subtitle final**: subtitles (+ optional `--bgm`) burned onto the neutral master `FULL.mp4`, which itself stays subtitle-free and BGM-free forever — it is the judge input and regression baseline; all cosmetics happen downstream.

## Multi-character skits

Beyond single-host videos, a 3-character office comedy has been replicated end-to-end: per-segment cast anchors + per-character persona clauses + a **hard character-count constraint** (without it the model hallucinates extra people — now baked into `patch_cast.py`); product images are stripped from pre-reveal segments to protect the suspense. Speaker/lip mis-assignment now has a full treatment (error rate 28% → 6% in practice): frame-level lip QC (`qc_lipsync.py`, source-video frames as ground truth) locates dirty segments → redraw loop (1-2 draws each, keep the better take) absorbs random errors → sticky errors (same line wrong twice) get timestamped for a manual fix in the draft. Hard lesson: **never** let a video model transcribe your AI footage as QC — it hallucinates entire lines. Dual-reverse note: the two reverse models fail in mirror image (Seed: timing/burned-in-text bleed; K3: entity hallucination — it has read a soap as a steamed bun), so trust K3 for camera timelines, trust Seed + **source-frame verification** for entities, and adjudicate field by field with `merge_reverse.py`.

## Notes

- Billing: Jimeng CLI uses a monthly credit pool; Volcano Ark bills per token (`--i2v-backend ark` offloads i2v shots).
- **Backend boundary (policy)**: Volcano rejects any reference image containing a human face (even AI-generated photoreal ones) — host/person segments are Jimeng-CLI-only; product-only segments can use either backend.
- **Compliance is on you**: no medical claims, no fabricated endorsements, real prices only.

## About the author

Wang Zili — in e-commerce since 2012, from the Taobao era through the Douyin era; running Douyin live-commerce full-time since 2019 (7 years and counting). Categories operated: **fresh food, jewelry & jade, bakery** — a messy mix, and exactly why the underlying logic became clear: whatever you sell, the algorithm always hands traffic to whoever converts it best.

- **Best single-session GMV** ¥3.06M · **largest single-session ad spend** ¥660K · full-service operating (data, campaigns, decisions)
- Runs his own paid-traffic campaigns, edits, and ROI reviews — sea cucumber, fruit, and soap were all tested with real money
- Works with agents like Claude Code daily and turns every hard-won lesson into a reusable skill; this repo grew exactly that way — not designed up front, but forced out one real order at a time
- More battle notes (paid traffic / live commerce ops, no courses sold): [5t9t.com](https://5t9t.com)

**WeChat** `hornonthebus` (for replication / AI video production topics — say why you're adding); [Issues](https://github.com/wangcanyu/daihuo-fanpai/issues) and PRs welcome

## License

MIT · Copyright (c) 2026 wangcanyu
