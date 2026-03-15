# Mobile Layout Change Log

This file records the responsive/mobile changes made on the game screen so the app can be returned to its earlier desktop-only layout if needed.

## Files changed

- `templates/game.html`
- `static/css/game.css`
- `static/js/game.js`

## Summary of changes

### 1. Bid form inputs changed to mobile-friendly numeric fields

File: `templates/game.html`

Changes:
- `quantity` input changed from `type="text"` to `type="number"`
- `price` input changed from `type="text"` to `type="number"`
- added `inputmode`, `min`, `step`, and placeholders
- label `for` attributes corrected to match the actual input ids

Previous version:

```html
<label for="bid">Quantity:</label>
<input type="text" id="quantity" name="quantity">
<label for="bid">Price:</label>
<input type="text" id="price" name="price">
```

Current version:

```html
<label for="quantity">Quantity:</label>
<input type="number" id="quantity" name="quantity" inputmode="numeric" min="0" step="1" placeholder="MW">
<label for="price">Price:</label>
<input type="number" id="price" name="price" inputmode="decimal" min="0" step="0.01" placeholder="$ / MWh">
```

Rollback:
- restore both inputs to `type="text"`
- remove `inputmode`, `min`, `step`, and placeholders
- restore the original label attributes if desired

### 2. Profit table wrapped in a scroll container

File: `templates/game.html`

Changes:
- wrapped `#playerProfitTable` in `<div class="table-scroll">`
- kept table contents intact

Rollback:
- remove the `.table-scroll` wrapper and return the table to its original position

### 3. Profit table headings were renamed

File: `templates/game.html`

Changes:
- header row changed from:
  - `Player`
  - `DA Profit`
  - `Change`
  - `RT Profit`
- to:
  - `Player Profit`
  - `DA`
  - `RT`
  - `Total`

Rollback:
- restore the original heading labels if you want the previous wording

### 4. Desktop layout widened and mobile layout added

File: `static/css/game.css`

Changes:
- added margin resets for headings/labels/paragraphs
- made form inputs full width
- added `input[type="number"]` styling
- changed `.gameplay-container` from a centered fixed horizontal row to a more flexible container with width limits and margin
- changed `.player-action-container`, `.profit-container`, and `.gains-container` sizing to use flexible widths
- changed `#assets-list` and `.form-container` to card-like blocks with padding and background
- added `.table-scroll`
- added table styling for `#playerProfitTable`
- changed `#myImage` from fixed `600x400` sizing to responsive sizing
- changed `.bidGraph` from `width: 80%` with large margins to a width-constrained responsive block
- added `#graph-controls` layout rules
- added a full mobile media query: `@media (max-width: 768px)`

Key original layout values before the responsive pass:

```css
.gameplay-container {
    display: flex;
    align-items: center;
    gap: 50px;
}

#bid-form {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 5px;
    width: 70%;
}

#myImage {
  width: 600px;
  height: 400px;
}

.bidGraph {
    margin: 50px 0px;
    width: 80%;
    height: 600px;
}
```

Rollback:
- remove the entire `@media (max-width: 768px)` block
- remove `.table-scroll`, `#playerProfitTable`, `#graph-controls`, `#default-form`, and `input[type="number"]` rules if you want a clean return
- restore the original layout values above
- remove card styling from `#assets-list` and `.form-container`

### 5. Plotly graph made responsive

File: `static/js/game.js`

Changes:
- added `responsive: true` to the Plotly config
- added explicit chart margins in both DA and RT layouts
- changed market price annotations to use fixed paper coordinates instead of chart-edge arrow placement

Rollback:
- remove `responsive: true`
- remove the added `margin` blocks from both layouts
- restore the old market price annotation behavior if preferred

### 6. Profit table rendering now has separate DA and RT views

File: `static/js/game.js`

Changes:
- the old single `profitRows` variable was split into:
  - `profitRowsDA`
  - `profitRowsRT`
- `updateLeader()` now swaps the table body depending on the graph phase
- gains lists are built from per-player cumulative maps instead of direct array mapping

This was not strictly required for mobile layout, but it is part of the current diff in `static/js/game.js`.

Rollback:
- restore the previous single `profitRows` rendering logic
- restore `profits_gains["profits_table"]`
- remove phase-based table swapping in `updateLeader()`

## Practical rollback options

### Option 1. Manual rollback

Use the sections above and revert only the parts you do not want.

### Option 2. Git-based rollback for these files only

If you decide later that you want to discard the current versions of only these files, compare or restore from git for:

- `templates/game.html`
- `static/css/game.css`
- `static/js/game.js`

Do that carefully because this repo already contains other unrelated changes.

## Notes

- This log only documents the mobile/responsive-related changes made in this session.
- No automatic rollback was performed.
- If needed, a cleaner next step would be to commit the current state to a branch so you can switch between versions safely.
