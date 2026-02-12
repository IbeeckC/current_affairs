# Memory

- Switched RT settlement to full re-clearing: RT profit uses `(P_RT - cost) * x_RT`, and round change is `RT_total - DA_total`.
- Added clearance thresholding to ignore tiny clears; now uses **relative threshold** with `EPS_CLEAR = max(ABS=1e-2, REL=1e-3 * demand)`.
- DA/RT market price and graph clearing use the same thresholded quantities to avoid epsilon noise.
- Tie-break clearing now evenly splits quantities across bids with the same price, and the graph places cleared quantities to the left of the demand bar with uncleared to the right.
