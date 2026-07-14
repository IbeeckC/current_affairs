# real_time_market.py
from curses import meta
import numpy as np
import scipy.sparse as sp
import osqp
from collections import defaultdict


def apply_event_to_bids(
    sorted_bids,
    demand,
    event,
    marketUnits,
    market_price_DA,
    selected_assets=None,
    selected_bids=None,
    event_demand_adjust=0.0,
    x_DA_by_id=None,
):
    selected_assets = selected_assets or []
    selected_bids = selected_bids or []

    event_bids = [dict(b) for b in sorted_bids]
    demand_new = float(demand)

    meta = {"event_tag": event, "event_name": "None"}

    eps = 1e-3
    x_DA_by_id = x_DA_by_id or {}
    cleared_ids = {str(bid_id) for bid_id, x in x_DA_by_id.items() if float(x) > eps}

    meta["notes"] = []
    meta["removed_ids"] = []
    meta["modified_ids"] = []
    meta["penalized_ids"] = []
    meta["demand_old"] = float(demand)

    if event == "none":
        meta["event_name"] = "None"

    elif event == "high_dem":
        meta["event_name"] = "Higher Demand"
        delta = abs(float(event_demand_adjust))
        demand_new = min(demand_new + delta, float(marketUnits))

    elif event == "low_dem":
        meta["event_name"] = "Lower Demand"
        delta = abs(float(event_demand_adjust))
        demand_new = max(demand_new - delta, 0.0)

    elif event == "high_bidder_remove":
        meta["event_name"] = "Remove Highest Cleared Bidder"
        cleared = [b for b in event_bids if str(b["id"]) in cleared_ids]
        if not cleared:
            meta["notes"].append("No DA-cleared bidders; nothing removed.")
        else:
            b_remove = max(cleared, key=lambda b: float(b["price"]))
            rid = b_remove["id"]
            event_bids = [b for b in event_bids if b["id"] != rid]
            meta["removed_ids"] = [rid]
    elif event == "low_bidder_remove":
        meta["event_name"] = "Remove Lowest Cleared Bidder"
        cleared = [b for b in event_bids if str(b["id"]) in cleared_ids]
        if not cleared:
            meta["notes"].append("No DA-cleared bidders; nothing removed.")
        else:
            b_remove = min(cleared, key=lambda b: float(b["price"]))
            rid = b_remove["id"]
            event_bids = [b for b in event_bids if b["id"] != rid]
            meta["removed_ids"] = [rid]

    elif event == "tax_coal&nat_gas":
        meta["event_name"] = "Tax on Coal and Natural Gas"
        coal_natGas = {
            "Coal", "Natural Gas (Open Cycle)", "Natural Gas (Combined Cycle)", "Diesel",
            "Lignite", "Fuel Oil", "Shale Oil", "Subbituminous coal",
            "Petroleom Coke", "Anthracite Coal", "Waste-to-Energy (Incineration)",
            "Biomass (Wood)", "Dual Fuel (Diesel & Natural Gas)"
        }
        for b in event_bids:
            if b["asset"] in coal_natGas:
                b["generation"] = float(b["generation"]) + 20.0

    elif event == "remove_renewable":
        meta["event_name"] = "Remove Renewable Generators"
        renewables = {
            "Wind (onshore)", "Wind (Offshore)", "Solar Photovoltaic", "Concentrated Solar Power",
            "Large-Scale Hydropower", "Geothermal", "Biogas (Landfills)", "Tidal Power",
            "Mini Hydropower", "Small Modular Reactors", "Energy Storage",
            "Biomass (Agricultural Waste/Peat)"
        }
        removed = [b for b in event_bids if b["asset"] in renewables]
        if removed:
            meta["removed_ids"] = [b["id"] for b in removed]
            event_bids = [b for b in event_bids if b["asset"] not in renewables]
        else:
            meta["notes"].append("No renewable bids to remove.")

    elif event == "renewable_subsidies":
        meta["event_name"] = "Renewable Subsidies"
        renewables = {
            "Wind (onshore)", "Wind (Offshore)", "Solar Photovoltaic", "Concentrated Solar Power",
            "Large-Scale Hydropower", "Geothermal", "Biogas (Landfills)", "Tidal Power",
            "Mini Hydropower", "Small Modular Reactors", "Energy Storage",
            "Biomass (Agricultural Waste/Peat)"
        }
        subsidized_ids = []
        for b in event_bids:
            if b["asset"] in renewables:
                b["generation"] = float(b["generation"]) - 10.0
                subsidized_ids.append(b["id"])
        if subsidized_ids:
            meta["modified_ids"] = subsidized_ids[:]
            meta["subsidized_ids"] = subsidized_ids
            meta["notes"].append("Renewable marginal costs reduced by 10 for RT settlement.")
        else:
            meta["notes"].append("No renewable bids received the subsidy.")

    elif event == "remove_by_asset_name":
        meta["event_name"] = "Removed Some Bidders by Asset Name"
        removed = [b for b in event_bids if b["asset"] in set(selected_assets)]
        if removed:
            meta["removed_ids"] = [b["id"] for b in removed]
        event_bids = [b for b in event_bids if b["asset"] not in set(selected_assets)]

    elif event == "remove_by_bid_price":
        meta["event_name"] = "Removed Some Bidders by Bid Price"
        selected_set = set(float(x) for x in selected_bids)
        removed = [b for b in event_bids if float(b["price"]) in selected_set]
        if removed:
            meta["removed_ids"] = [b["id"] for b in removed]
        event_bids = [b for b in event_bids if float(b["price"]) not in selected_set]

    elif event == "penalty_high_bid":
        meta["event_name"] = "Regulator Penalty on Cleared High Bidders"
        # Penalize DA-cleared bidders whose price is an outlier among cleared prices
        cleared_bids = [b for b in event_bids if str(b["id"]) in cleared_ids]
        cleared_prices = [float(b["price"]) for b in cleared_bids]
        if cleared_prices:
            mean_p = float(np.mean(cleared_prices))
            std_p = float(np.std(cleared_prices))
            threshold = mean_p + (1.25 * std_p)
            for b in cleared_bids:
                if float(b["price"]) > threshold:
                    b["price"] = 1.0  # force near-zero price to penalize manipulation
                    meta["penalized_ids"].append(b["id"])
                    meta["modified_ids"].append(b["id"])
            if not meta["penalized_ids"]:
                meta["notes"].append("No DA-cleared bidders exceeded the outlier threshold.")
        else:
            meta["notes"].append("No DA-cleared bidders; no outlier check.")

    elif event == "pay_as_bid":
        meta["event_name"] = "Pay As Bid"
        meta["pay_as_bid"] = True
        meta["notes"].append("RT settlement uses each cleared bid's own price.")

    else:
        meta["event_name"] = "Unrecognized Event"
        meta["unknown_event"] = True

    meta["demand_new"] = float(demand_new)
    meta["demand_delta"] = float(demand_new) - float(demand)
    print(f"[EVENT] removed_ids={meta.get('removed_ids')} modified_ids={meta.get('modified_ids')}")

    return event_bids, demand_new, meta

def solve_real_time_dispatch_qp(event_bids, demand_new, x_DA_by_id, lambda_reg=1e-4):

    n = len(event_bids)
    if n == 0:
        return {
            "y_by_id": {},
            "x_rt_by_id": {},
            "x_ID_by_id": {},
            "P_RT": None,
            "P_ID": None,
            "delta_D": float(demand_new),
            "status": "no_bids"
        }

    prices = np.array([float(b["price"]) for b in event_bids], dtype=float)
    caps   = np.array([float(b["quantity"]) for b in event_bids], dtype=float)
    x_DA   = np.array([float(x_DA_by_id.get(b["id"], 0.0)) for b in event_bids], dtype=float)

    demand_new = float(demand_new)
    delta_D = demand_new - float(np.sum(x_DA))

    # If delta_D is ~0, real-time does nothing
    if abs(delta_D) < 1e-9:
        y = np.zeros(n, dtype=float)
        x_RT = x_DA.copy()
        P_RT = compute_rt_price_from_dispatch(event_bids, x_RT, demand_new)
        return _package_rt_results(event_bids, y, x_RT, P_RT, delta_D, status="no_adjustment")

    # QP: (1/2) y^T P y + q^T y
    P = sp.eye(n, format="csc") * (2.0 * float(lambda_reg))
    q = prices

    # Constraints: box + equality
    A = sp.vstack([
        sp.eye(n, format="csc"),
        sp.csc_matrix(np.ones((1, n)))
    ], format="csc")

    l = np.hstack([-x_DA, delta_D])
    u = np.hstack([caps - x_DA, delta_D])

    prob = osqp.OSQP()
    prob.setup(P=P, q=q, A=A, l=l, u=u, verbose=False)
    res = prob.solve()

    y = np.asarray(res.x, dtype=float)
    # Numerical cleanup
    y[np.abs(y) < 1e-9] = 0.0

    # Economic cleanup: clamp tiny adjustments ("dust") and re-balance delta
    EPS_CLEAR = 1e-3
    y[np.abs(y) < EPS_CLEAR] = 0.0
    gap = delta_D - float(np.sum(y))
    if gap > 0.0:
        # Upward adjustment: fill gap in merit order (cheapest first)
        order = np.argsort(prices)
        for i in order:
            available = float(caps[i]) - float(x_DA[i]) - float(y[i])
            if available <= 0.0:
                continue
            take = min(available, gap)
            y[i] += take
            gap -= take
            if gap <= 1e-9:
                break
    elif gap < 0.0:
        # Downward adjustment: curtail in reverse merit order (most expensive first)
        order = np.argsort(prices)[::-1]
        gap = -gap
        for i in order:
            available = float(x_DA[i]) + float(y[i])  # how much can be reduced
            if available <= 0.0:
                continue
            take = min(available, gap)
            y[i] -= take
            gap -= take
            if gap <= 1e-9:
                break

    x_RT = x_DA + y
    x_RT[np.abs(x_RT) < 1e-9] = 0.0

    # Price rule: marginal final dispatch price
    P_RT = compute_rt_price_from_dispatch(event_bids, x_RT, demand_new)

    return _package_rt_results(event_bids, y, x_RT, P_RT, delta_D, status=res.info.status)

def compute_rt_price_from_adjustment(event_bids, y, delta_D, eps=1e-9):
    prices = np.array([float(b["price"]) for b in event_bids], dtype=float)

    if delta_D > eps:
        # upward redispatch: y > 0 contributors, marginal is the last needed upward MW
        contrib = [(prices[i], y[i]) for i in range(len(y)) if y[i] > eps]
        if not contrib:
            return None
        contrib.sort(key=lambda t: t[0])  # cheapest upward first
        running = 0.0
        for p, amt in contrib:
            running += amt
            if running >= delta_D - eps:
                return float(p)
        return float(contrib[-1][0])

    if delta_D < -eps:
        # downward redispatch: y < 0 contributors, marginal is the last needed downward MW
        needed = -delta_D
        contrib = [(prices[i], -y[i]) for i in range(len(y)) if y[i] < -eps]
        if not contrib:
            return None
        contrib.sort(key=lambda t: t[0])  # game rule: lowest-price units adjust first
        running = 0.0
        for p, amt in contrib:
            running += amt
            if running >= needed - eps:
                return float(p)
        return float(contrib[-1][0])

    return None

def compute_rt_price_from_dispatch(event_bids, x_RT, demand, eps_clear=1e-3, eps_dem=1e-6):
    demand = float(demand)
    x_RT = np.asarray(x_RT, dtype=float)

    cum = 0.0
    market_price = None

    for i, bid in enumerate(event_bids):
        xi = float(x_RT[i])
        if xi > eps_clear:
            cum += xi
            market_price = float(bid["price"])
            if cum >= demand - eps_dem:
                return market_price

    cleared_prices = [
        float(event_bids[i]["price"])
        for i in range(len(event_bids))
        if float(x_RT[i]) > eps_clear
    ]
    if cleared_prices:
        return max(cleared_prices)

    return float(min(float(b["price"]) for b in event_bids)) if event_bids else 0.0

def _package_rt_results(event_bids, y, x_RT, P_RT, delta_D, status="solved"):
    y_by_id = {event_bids[i]["id"]: float(y[i]) for i in range(len(event_bids))}
    x_by_id = {event_bids[i]["id"]: float(x_RT[i]) for i in range(len(event_bids))}
    return {
        "y_by_id": y_by_id,
        "x_rt_by_id": x_by_id,
        "x_ID_by_id": x_by_id,
        "P_RT": P_RT,
        "P_ID": P_RT,
        "delta_D": float(delta_D),
        "status": status
    }

# Backwards-compatible wrappers and keys 
def solve_intraday_qp(event_bids, demand_new, x_DA_by_id, lambda_reg=1e-4):
    return solve_real_time_dispatch_qp(event_bids, demand_new, x_DA_by_id, lambda_reg=lambda_reg)

def compute_intraday_price_from_adjustment(event_bids, y, delta_D, eps=1e-9):
    return compute_rt_price_from_adjustment(event_bids, y, delta_D, eps=eps)

def _package_intraday_results(event_bids, y, x_ID, P_ID, delta_D, status="solved"):
    return _package_rt_results(event_bids, y, x_ID, P_ID, delta_D, status=status)

def settle_day_ahead(bids, P_DA, x_DA_by_id):
    """
    DA settlement only:
      gain_DA = (P_DA - cost) * x_DA
    Mutates PlayerData via add_to_profit.
    Returns:
      - per_bid list (for UI tables)
      - per_player list (for leaderboards)
    """
    per_player = defaultdict(float)
    per_bid = []

    P_DA = float(P_DA)

    print(f"[PROFIT][DA] P_DA={P_DA}")

    for b in bids:
        bid_id = b["id"]
        cost = float(b["generation"])
        x = float(x_DA_by_id.get(bid_id, 0.0))

        gain = (P_DA - cost) * x
        if abs(gain) < 1e-12:
            gain = 0.0

        print(
            f"[PROFIT][DA][BID] player={b['player']} id={bid_id} "
            f"cost={cost} x_DA={x} gain_DA={gain}"
        )

        b["data"].add_to_profit(gain)
        per_player[b["player"]] += gain

        per_bid.append({
            "player": b["player"],
            "id": bid_id,
            "gain_DA": gain,
            "x_DA": x,
            "mc": cost
        })

    per_player_list = [{"player": p, "gain": g} for p, g in per_player.items()]
    print(f"[PROFIT][DA][TOTALS] {per_player_list}")
    return per_bid, per_player_list
def settle_real_time(bids, P_RT, y_by_id):
    """
    RT incremental settlement only (deviations):
      gain_RT = (P_RT - cost) * y
    Mutates PlayerData via add_to_profit.
    Returns:
      - per_bid list
      - per_player list
    """
    per_player = defaultdict(float)
    per_bid = []

    P_RT = float(P_RT)

    print(f"[PROFIT][RT] P_RT={P_RT}")

    for b in bids:
        bid_id = b["id"]
        cost = float(b["generation"])
        y = float(y_by_id.get(bid_id, 0.0))

        gain = (P_RT - cost) * y
        if abs(gain) < 1e-12:
            gain = 0.0

        print(
            f"[PROFIT][RT][BID] player={b['player']} id={bid_id} "
            f"cost={cost} y={y} gain_RT={gain}"
        )

        b["data"].add_to_profit(gain)
        per_player[b["player"]] += gain

        per_bid.append({
            "player": b["player"],
            "id": bid_id,
            "gain_RT": gain,
            "y": y,
            "mc": cost
        })

    per_player_list = [{"player": p, "gain": g} for p, g in per_player.items()]
    print(f"[PROFIT][RT][TOTALS] {per_player_list}")
    return per_bid, per_player_list

def compute_real_time_full(
    bids,
    P_RT,
    x_rt_by_id,
    penalized_ids=None,
    penalized_settlement_price=1.0,
    pay_as_bid=False,
):
    """
    RT full re-clearing settlement:
      - normal bids:    gain_RT_full = (P_RT - cost) * x_RT
      - penalized bids: gain_RT_full = (P_pen - cost) * x_RT
      - pay-as-bid:     gain_RT_full = (bid_price - cost) * x_RT
    Does NOT mutate PlayerData (caller applies deltas).
    Returns:
      - per_bid list
      - per_player list
    """
    per_player = defaultdict(float)
    per_bid = []

    P_RT = float(P_RT)
    P_pen = float(penalized_settlement_price)
    penalized_set = {str(bid_id) for bid_id in (penalized_ids or [])}
    print(f"[PROFIT][RT_FULL] P_RT={P_RT}")

    for b in bids:
        bid_id = b["id"]
        cost = float(b["generation"])
        x_rt = float(x_rt_by_id.get(bid_id, 0.0))

        is_penalized = str(bid_id) in penalized_set
        if pay_as_bid:
            settlement_price = float(b["price"])
        else:
            settlement_price = P_pen if is_penalized else P_RT
        gain = (settlement_price - cost) * x_rt
        if abs(gain) < 1e-12:
            gain = 0.0

        print(
            f"[PROFIT][RT_FULL][BID] player={b['player']} id={bid_id} "
            f"cost={cost} x_RT={x_rt} settlement_price={settlement_price} "
            f"penalized={is_penalized} gain_RT_full={gain}"
        )

        per_player[b["player"]] += gain
        per_bid.append({
            "player": b["player"],
            "id": bid_id,
            "gain_RT_full": gain,
            "x_RT": x_rt,
            "mc": cost,
            "settlement_price": settlement_price,
            "penalized": is_penalized
        })

    per_player_list = [{"player": p, "gain": g} for p, g in per_player.items()]
    print(f"[PROFIT][RT_FULL][TOTALS] {per_player_list}")
    return per_bid, per_player_list
