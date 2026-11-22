---
name: sprite-sheet-kit
description: Generic, reusable workflow for making game/app character sprite sheets with an image-generation AI. A project JSON (character + poses) is turned by tools/sprite_kit.py into row prompts, a labelled guide, a spec, and - after the image AI has drawn the rows - a master sheet, atlas.json, single frames, checks and recoloured variants. Use when starting sprites for a new application, adding poses, or regenerating/recolouring an existing character (any character).
---

# Sprite sheet kit

## Why this approach

| Naive approach | Problem | This kit |
|---|---|---|
| Hand-written prompts per game, character text copied into every row | Drift between files, tedious to extend | **One project JSON** is the single source of truth; prompts, guide and spec are generated |
| "Transparent PNG, 8:1 aspect" | Most image AIs cannot output real alpha or an 8:1 canvas; you get white or checker backgrounds and halos | Ask for a **flat key colour background** (default magenta, project-chosen); the assembler mattes it out and un-mixes the fringe. Real alpha is also accepted |
| Rows cut into equal cells | One misplaced frame ruins the row | **Gap detection** finds the frames (merging detached sparkles); equal slicing is the fallback with a warning |
| Per-frame scale/feet alignment unspecified | Characters jitter and change size between frames | Uniform **row scale** tied to `standing_height`, feet on a ground anchor, `air` frames lifted or centred, centroid-based horizontal alignment |
| "Don't send an example image" | Text-only consistency is the weakest part with image AIs | Step 0 generates a **character model sheet**; approve it and attach it to every row. Text prompts still work alone |
| Frames addressed by row/column in code | Fragile when rows are added | `atlas.json` gives **frame ids and animations** (`idle`, `cheer`...); loader draws by name |
| Manual eyeballing | Red leaks, cropping and size drift found late | `check` flags empty/cropped frames, leftover key colour, off-scale standing height; `recolor` previews every team colour |

## Files
- `tools/sprite_kit.py` - build / assemble / check / recolor (Pillow + numpy)
- `tools/sprite_loader.js` - dependency-free canvas loader (draw by id, animate, recolour in the browser)
- `projects/_template.json` - copy for a new character
- Generated into `out/<name>/`: `prompts/`, `all_prompts.md`, `guide_blank.png`, `SPEC.md`

## Workflow for a new application (Claude's checklist)
1. Work out what the app needs from the app itself (its code, design and the poses it already references); only ask the user about what you cannot determine. Pick: frame size, standing height, ground anchor (about 8 px above the bottom), key colour not used by the character, tint colour if recolouring is needed.
2. Copy `projects/_template.json`; write the **character** paragraph (self-contained, hex colours per part, outline, shading, light, camera, facing) and the **rows**. Rules of thumb:
   - 4 or 8 frames per row, max `cols` (8). A row holds related poses, loops of 4.
   - Each frame `desc` is complete on its own: pose + expression + props. Loop frames say what changes ("arm swings outward").
   - Never ask the image AI for rotated art; use separate head/body poses, or let the app rotate or mirror upright frames. Turnarounds: 5 views and mirror.
   - Mark one upright neutral frame `"stand": true` (usually Idle 1) so rows can be scale-normalised.
   - `"anchor": "air"` for jumping/tumbling frames (`lift` px or project `air_center`).
   - With a tint colour, allow it on the recolourable parts ONLY and ban red/pink/orange from effects.
3. `python3 tools/sprite_kit.py build projects/<name>.json` then give the user `out/<name>/prompts/` (or `all_prompts.md`) and `guide_blank.png`.
4. User runs `00_reference` in the image AI, approves it, then pastes each row prompt **in the same chat**, attaching the reference image. Saves `row_00.png`, `row_01.png`, ... into one folder. Fix drift in-chat ("frame 3 should look further left", "keep the same character as the reference").
5. `python3 tools/sprite_kit.py assemble projects/<name>.json <rows_folder> -o out/<name>` then `check`. Look at `preview.png`.
   - Wrong size on one row: `--row-scale run=1.1`. Frames glued together: `--equal`. Disable the standing-height tie: `--no-auto-scale`.
   - Regenerate only the bad row and re-run assemble.
6. With a tint: `recolor` and check `recolor_preview.png` for leftover red on non-red variants (fix by removing red from the art, not by loosening the tint tolerance).
7. Ship `<name>_master.png` + `atlas.json`; use `tools/sprite_loader.js` in HTML apps.

## Adding poses later
Add a frame or row to the project JSON, re-run `build`, generate only the new row, re-run `assemble` with all rows present. Existing frame ids stay stable, so app code does not change.

## Acceptance checklist
- [ ] Same character, outline, colours, scale in every row
- [ ] Rows have the right number of frames in the right order (compare to `guide_blank.png`)
- [ ] Tint colour only on the intended parts; recolour preview clean
- [ ] No key-colour fringe, nothing cropped, no text or guide marks (`check` = 0 issues)
- [ ] Standing height and feet line are stable across rows in `preview.png`
