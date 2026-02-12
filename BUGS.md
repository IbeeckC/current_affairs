# Bug Log

## 1) Removal Event: Lowest Cleared Bidder Still Retains Profit
- Symptom: When the "remove lowest cleared bidder" event triggers, the removed bidder's prior profit is still counted.
- Expected: Removed bidder should not retain profit for the round (per desired policy).
- Notes: Reported during rounds where the removal event triggers.

## 2) Uncleared Bidder Setting Market Price After Event
- Symptom: An uncleared bidder appears to set the RT market price after a removal event.
- Evidence (log excerpt): In `remove_by_bid_price`, DA clearing shows one bid with near‑zero `x_DA`, then after event, the remaining bids include an uncleared bid that sets `P_RT`.
- Expected: Market price should be set by marginal cleared bids only.

## 3) Graph Misrepresents Tie‑Break Clearing
- Symptom: Tie‑break clears equally across two generators, but the graph displays non‑cleared quantity as cleared for one generator, and some cleared bids as not cleared.
- Expected: Graph should match actual cleared quantities after tie‑break.
- Evidence: Screenshot from “Electricity Market Round 2 (Lower Demand)” shows bars not aligned with cleared split.

## 4) RT Solve Infeasible After Removal (Demand > Supply)
- Symptom: When a removal event leaves insufficient supply, RT solve reports `primal infeasible` but still outputs a full dispatch and price.
- Expected: Either cap dispatch at available supply or skip RT settlement with a clear fallback price/behavior.
- Notes: Seen when removing bidders causes demand to exceed remaining capacity; affects price, profits, and graph consistency.
