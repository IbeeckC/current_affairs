
from flask import Flask, request, session, render_template, redirect, url_for
from flask_socketio import SocketIO, Namespace, join_room, leave_room, disconnect, emit
from functools import wraps
from dotenv import load_dotenv
from urllib.parse import parse_qs
from scipy.optimize import linprog
import copy
import numpy as np
import scipy.sparse as sp
from model import *
from real_time_adjustment import apply_event_to_bids, solve_real_time_dispatch_qp, settle_day_ahead, settle_real_time, compute_real_time_full
import osqp # for qpsolvers

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = "secret_key"
socketio = SocketIO(app, cors_allowed_origins='*', manage_session=False)

room_manager = RoomManager()

DEBUG = True

def dbg(*args):
    if DEBUG:
        print(*args)

EPS_CLEAR_ABS = 1e-2
EPS_CLEAR_REL = 1e-3

def clearance_eps(demand):
    try:
        dem = float(demand)
    except (TypeError, ValueError):
        dem = 0.0
    return max(EPS_CLEAR_ABS, EPS_CLEAR_REL * dem)

def apply_clearance_threshold(x, demand):
    eps = clearance_eps(demand)
    x = np.asarray(x, dtype=float)
    x[x < eps] = 0.0
    return x

def compute_market_price_from_clearing(bids, x, demand, fallback=0.0):
    eps = clearance_eps(demand)
    market_price = None
    for i, b in enumerate(bids):
        if float(x[i]) >= eps:
            market_price = float(b["price"])
    if market_price is None:
        return float(fallback)
    return float(market_price)

def allocate_by_price_equal_split(bids, demand):
    remaining = max(float(demand), 0.0)
    x = np.zeros(len(bids), dtype=float)
    i = 0
    eps_price = 1e-9

    while i < len(bids) and remaining > 0.0:
        price = float(bids[i]["price"])
        group = []
        group_qty = 0.0

        while i < len(bids) and abs(float(bids[i]["price"]) - price) <= eps_price:
            group.append(i)
            group_qty += float(bids[i]["quantity"])
            i += 1

        if group_qty <= 0.0:
            continue

        if remaining >= group_qty:
            for idx in group:
                x[idx] = float(bids[idx]["quantity"])
            remaining -= group_qty
            continue

        # Partial clearing within a price-tied group: split evenly with caps.
        remaining_group = remaining
        active = group[:]
        while active and remaining_group > 0.0:
            share = remaining_group / len(active)
            next_active = []
            for idx in active:
                cap = float(bids[idx]["quantity"])
                alloc = min(share, cap - x[idx])
                if alloc > 0.0:
                    x[idx] += alloc
                    remaining_group -= alloc
                if x[idx] + 1e-12 < cap:
                    next_active.append(idx)
            if len(next_active) == len(active):
                # No progress due to numerical noise
                break
            active = next_active

        remaining = 0.0

    return x

def linprog_to_graph(in_data, in_linprog, demand, marketPrice):
    import numpy as np

    x = apply_clearance_threshold(in_linprog, demand)

    assets, players, costs = [], [], []
    barHeight, xList, widthBar, colors = [], [], [], []

    total_cleared = float(np.sum(x))
    cur_cleared = 0.0
    cur_uncleared = total_cleared

    for i, b in enumerate(in_data):
        q = float(b["quantity"])
        cleared = min(float(x[i]), q)
        leftover = max(q - cleared, 0.0)

        # --- CLEARED PORTION ---
        if cleared > 0:
            assets.append(b["asset"])
            players.append(b["player"])
            costs.append(b["generation"])
            barHeight.append(b["price"])
            xList.append(cur_cleared + cleared / 2)
            widthBar.append(cleared)
            colors.append(
                f'rgba({b["color"][0]}, {b["color"][1]}, {b["color"][2]}, 1)'
            )
            cur_cleared += cleared

        # --- LEFTOVER (UNCLEARED) PORTION ---
        if leftover > 0:
            assets.append(b["asset"])
            players.append(b["player"])
            costs.append(b["generation"])
            barHeight.append(b["price"])
            xList.append(cur_uncleared + leftover / 2)
            widthBar.append(leftover)
            colors.append(
                f'rgba({b["color"][0]}, {b["color"][1]}, {b["color"][2]}, 0.25)'
            )
            cur_uncleared += leftover

    return {
        "barHeight": barHeight,
        "xList": xList,
        "widthBar": widthBar,
        "colors": colors,
        "demand": float(demand),
        "marketPrice": float(marketPrice),
        "players": players,
        "costs": costs,
        "assets": assets,
    }

def qp_day_ahead_clearing(sorted_bids, demand, lambda_reg=1e-4):
    n = len(sorted_bids)
    
    # Extract prices and capacities
    prices = np.array([bid['price'] for bid in sorted_bids], dtype=float)
    capacities = np.array([bid['quantity'] for bid in sorted_bids], dtype=float)
    dem = float(demand)
    
    # Check feasibility
    total_supply = np.sum(capacities)
    if total_supply < dem - 1e-6:
        print(f"WARNING: Insufficient supply! Supply={total_supply:.1f}, Demand={dem:.1f}")
        x = capacities.copy()
        market_price = np.max(prices)  # Scarcity pricing
        
        x_by_id = {sorted_bids[i]["id"]: float(x[i]) for i in range(n)}

        # Store results in bids
        for i, bid in enumerate(sorted_bids):
            bid['x_cleared'] = float(x[i])
            bid['x_DA'] = float(x[i])
        
        return {
            'x': x,
            'x_by_id': x_by_id,
            'market_price': market_price,
            'total_cleared': float(np.sum(x)),
            'bids': sorted_bids,
            'is_scarcity': True,
            'status': 'scarcity'
        }
    
    # QP formulation
    P = sp.eye(n, format='csc') * (2.0 * lambda_reg)
    q = prices
    
    A = sp.vstack([
        sp.eye(n, format='csc'),           # Box constraints (n rows)
        sp.csc_matrix(np.ones((1, n)))     # Demand constraint (1 row)
    ], format='csc')
    
    l = np.hstack([np.zeros(n), dem])      # Lower bounds
    u = np.hstack([capacities, dem])       # Upper bounds
    
    # Solve
    prob = osqp.OSQP()
    prob.setup(P=P, q=q, A=A, l=l, u=u, verbose=False)
    res = prob.solve()

    if res.info.status_val not in (1, 2):  # 1=solved, 2=solved inaccurate (OSQP)
        print(f"WARNING: OSQP did not solve DA properly. status={res.info.status}")
        # Safe fallback: dispatch by merit order greedily
        x = np.zeros(n, dtype=float)
        remaining = dem
        for i in range(n):
            take = min(capacities[i], remaining)
            x[i] = take
            remaining -= take
            if remaining <= 1e-9:
                break
        final_price = compute_uniform_price_from_dispatch(sorted_bids, x, dem)
        x_by_id = {sorted_bids[i]["id"]: float(x[i]) for i in range(n)}
        for i, bid in enumerate(sorted_bids):
            bid['x_cleared'] = float(x[i])
            bid['x_DA'] = float(x[i])
        return {
            'x': x,
            'x_by_id': x_by_id,              # NEW
            'market_price': float(final_price),
            'shadow_price': None,
            'total_cleared': float(np.sum(x)),
            'bids': sorted_bids,
            'is_scarcity': False,
            'status': res.info.status
        }
    # Extract solution
    x = np.maximum(np.asarray(res.x, dtype=float), 0.0)

    # Numerical cleanup: keep very small values at 0
    eps_x = 1e-9
    x[np.abs(x) < eps_x] = 0.0

    # Economic cleanup: clamp "dust" allocations and re-balance to demand
    eps_clear = clearance_eps(dem)
    x[np.abs(x) < eps_clear] = 0.0
    gap = dem - float(np.sum(x))
    if gap > 0.0:
        # Refill gap in merit order while respecting capacities
        for i in range(n):
            available = float(capacities[i]) - float(x[i])
            if available <= 0.0:
                continue
            take = min(available, gap)
            x[i] += take
            gap -= take
            if gap <= 1e-9:
                break
    
    dual_vars = res.y
    marginal_price_from_dual = float(dual_vars[n])  # Dual of demand constraint
    
    # Alternative: compute market price from merit order of cleared units
    # This matches economic theory: marginal unit sets the price
    market_price = compute_uniform_price_from_dispatch(sorted_bids, x, dem)
    final_price = market_price

    x_by_id = {sorted_bids[i]["id"]: float(x[i]) for i in range(n)}
    
    # Store results in bid objects
    for i, bid in enumerate(sorted_bids):
        bid['x_cleared'] = float(x[i])
        bid['x_DA'] = float(x[i])
    
    return {
        'x': x,
        'x_by_id': x_by_id,
        'market_price': final_price,
        'shadow_price': marginal_price_from_dual,
        'total_cleared': float(np.sum(x)),
        'bids': sorted_bids,
        'is_scarcity': False,
        'status': res.info.status
    }


def compute_uniform_price_from_dispatch(sorted_bids, x, demand):
    eps_clear = clearance_eps(demand)
    EPS_DEM = 1e-6

    demand = float(demand)
    x = np.asarray(x, dtype=float)

    # cumulative cleared using economic threshold
    cum = 0.0
    market_price = None

    for i, bid in enumerate(sorted_bids):
        xi = float(x[i])
        if xi > eps_clear:
            cum += xi
            market_price = float(bid["price"])  
            if cum >= demand - EPS_DEM:
                return market_price

    # If we didn't "reach demand" under EPS_CLEAR, choose highest price among economically-cleared units.
    cleared_prices = [float(sorted_bids[i]["price"]) for i in range(len(sorted_bids)) if float(x[i]) > eps_clear]
    if cleared_prices:
        return max(cleared_prices)

    # If nothing is economically cleared, pick the minimum offer price (or 0) to avoid nonsense.
    return float(min(float(b["price"]) for b in sorted_bids)) if sorted_bids else 0.0


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        room = session.get("room")
        name = session.get("name")
        
        current_room = room_manager.get_room(room)
        if room is None or name is None or current_room is None:  # Check if user is in session
            if request.endpoint != "index":
                print("Redirect to index")
                return redirect(url_for("index"))  # Redirect to login if not logged in
            
        elif not current_room.get_room_status(): # Check if game is still in lobby
            if request.endpoint != "lobby":
                print("Redirect to lobby")
                return redirect(url_for("lobby"))
            
        elif current_room.get_room_status():
            if request.endpoint != "game":
                print("Redirect to game")
                return redirect(url_for("game"))
    
        return f(*args, **kwargs)  # Otherwise, proceed to the game
    return decorated_function

@app.route("/logout")
def logout():
    session.pop("name", None)  
    return redirect(url_for("index"))

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        username = request.form.get('username')
        action = request.form.get('action')

        if not username or username.strip() == "":
            context = { "err": True, "msg": "Enter a valid username" }
            return render_template('index.html', ctx=context)
        if action == "Create Room":
            room_code = room_manager.create_room(username)
            session["room"] = room_code
            session["name"] = username

            print("Create Room")
            return redirect(url_for('lobby'))
        elif action == "Join Room":
            code = request.form.get('join-code')
            joining_room = room_manager.get_room(code)

            if joining_room is not None and joining_room.get_room_status():
                context = { "err": True, "msg": "Game Started" }
                return render_template('index.html', ctx=context)
            
            if joining_room is not None:
                player_usernames = [player.username for player in room_manager.get_username_from_players_room(code)]
                admin_username = room_manager.get_room(code).admin.username
                if username in player_usernames or username == admin_username: # Ensure usernames are unique
                    context = { "err": True, "msg": "Enter an Unused Username" }
                    return render_template('index.html', ctx=context)
                else:
                    session["room"] = code
                    session["name"] = username

                return redirect(url_for('lobby'))
            else: 
                context = { "err": True, "msg": "Room does not exist" }
                return render_template('index.html', ctx=context)
    context = { "err": False, "msg": "" }
    return render_template('index.html', ctx=context)

@app.route('/lobby', methods=['GET', 'POST'])
@login_required
def lobby():
    room = session.get("room")
    name = session.get("name")
    
    if request.method == "POST":
        action = request.form.get('action')

        if action == 'leave': 
            return redirect(url_for('logout'))
    
    lobby_room = room_manager.get_room(room)

    context = { "room": room, "is_admin": name == lobby_room.get_admin(), "admin": lobby_room.get_admin() }

    return render_template('lobby.html', ctx=context)

@app.route('/game', methods=['GET', 'POST'])
@login_required
def game():
    room = session.get("room")
    name = session.get("name")
    game_room = room_manager.get_room(room)

    if game_room is None:
        return redirect(url_for("index"))
    
    context={ "room": game_room.get_json_room(), "is_admin": name == game_room.get_admin() }
    return render_template('game.html', ctx=context)

class LobbyNamespace(Namespace):
    def on_connect(self):
        room = session.get("room")
        name = session.get("name")
        print(f"{name} joined room {room}")
        lobby_room = room_manager.get_room(room)

        if lobby_room is not None and name:
            print(f"User {name} joined room {room}")
            join_room(room)

            if lobby_room.get_admin() != name:
                lobby_room.add_player(name)

            socketio.emit("user_change", lobby_room.get_json_room(), namespace='/lobby', to=room)
        else:
            socketio.emit("player_left", {'msg': "gone"}, namespace='/lobby', to=request.sid)

    def on_disconnect(self):
        room = session.get("room")
        name = session.get("name")
        lobby_room = room_manager.get_room(room)

        print(f"User {name} left room {room}")
        leave_room(room)

        if lobby_room is None or lobby_room.get_room_status():
            return
        
        if lobby_room.get_admin() == name:
            print(f"Delete room {room}")
            socketio.emit("player_left", {'msg': "gone"}, namespace='/lobby', to=room)
            room_manager.delete_room(room)
            return

        lobby_room.remove_player(name)

        socketio.emit("user_change", lobby_room.get_json_room(), namespace='/lobby', to=room)

    def on_start_game(self, data):
        room = session.get("room")
        lobby_room = room_manager.get_room(room)
        
        lobby_room.create_players_data()
        lobby_room.set_room_status(True)
        
        socketio.emit('game_start', {'message': 'Game is starting!'}, namespace='/lobby', to=room)


class GameNamespace(Namespace):
    def on_connect(self):
        room = session.get("room")
        name = session.get("name")
        sid = request.sid

        join_room(room)
        game_room = room_manager.get_room(room)

        if game_room is not None:
            print(f"User {name} SID updated: {sid}")
            game_room.set_sid_from_players(name, sid)
        else:
            disconnect()

    def on_disconnect(self): #" took out reason"
        room = session.get("room")
        leave_room(room)
        print("Game Disconnect")
    
    def on_get_stats(self):
        room = session.get("room")
        name = session.get("name")
        game_room = room_manager.get_room(room)

        if game_room.get_admin() == name:
            return
        
        data = game_room.get_player_data(name)
        player_sid = game_room.get_sid_from_players(name)

        data["currentRound"] = game_room.get_current_round()
        
        socketio.emit('send_stats', data, namespace='/game', to=player_sid)
        print(f"Sent Stats {data} to {name}")

    def on_start_next_round(self):
        print("Start Next Round event received")
        room = session.get("room")
        game_room = room_manager.get_room(room)
        players = game_room.get_json_room()["players"]
        for p in players:
            name = p["username"]
            if not game_room.get_player_bid_status(name): # havent bid yet
                    game_room.get_player_data_object(name).set_bid_status(True)

                    player_bid = game_room.get_player_data_object(name).get_player_single_bid()
                    player_bid.set_price_quantity(player_bid.get_generation(), player_bid.get_units())

                    # game_room.get_player_data_object(name).get_player_single_bid().set_price_quantity(0.0, 0.0)
                    socketio.emit('bid_status', {'message': 'Bid successful!'}, namespace='/game', to=game_room.get_sid_from_players(name))

                    marketUnits = 0
                    allBid = game_room.has_all_players_bid()
                    if allBid:
                        marketUnits = game_room.get_total_bid_units()

                    # Get list of asset names and the bid prices
                    all_bids = game_room.get_json_all_bids()
                    asset_names = []
                    bid_prices = []
                    for bid in all_bids:
                        asset_names.append(bid["asset"])
                        bid_prices.append(bid["price"])

                    print(f"Submit Bid id: {game_room.get_player_data_object(name).get_id()}")
                    data = {
                        "allBid": allBid,
                        "name": name,
                        "player_id": game_room.get_player_data_object(name).get_id(),
                        "marketUnits": marketUnits,
                        "assetNames": asset_names,
                        "bidPrices": bid_prices
                    }
                    print(f"Send data to room: {data}")
                    socketio.emit('all_bids_status', data, namespace='/game', to=room)
            
                    print(f"{name} submit data: 0 quantity and price")


    def on_submit_bid(self, data):
        print("Submit Bid")
        room = session.get("room")
        name = session.get("name")
        game_room = room_manager.get_room(room)

        if game_room is None:
            disconnect()

        parsed_data = parse_qs(data.get('data', ''))
        parsed_data_clean = {}

        for key, values in parsed_data.items():
            try:
                # Try converting the value to an int
                parsed_data_clean[key] = int(values[0])
            except ValueError:
                try:
                    # If that fails, try converting the value to a float
                    parsed_data_clean[key] = float(values[0])
                except ValueError:
                    # If both conversions fail, keep it as original type
                    parsed_data_clean[key] = values[0]
        
        # Have They Already Placed a Bid
        if game_room.get_player_bid_status(name):
            socketio.emit('bid_status', {'message': 'Already placed bid. Wait till next round!'}, namespace='/game', to=game_room.get_sid_from_players(name))
            return

        if 'default_quantity' in parsed_data_clean:
            player_bid = game_room.get_player_data_object(name).get_player_single_bid()
            player_bid.set_price_quantity(player_bid.get_generation(), player_bid.get_units())
        else:
            # Error Check data
            if 'quantity' not in parsed_data:
                socketio.emit('bid_status', {'message': f'Enter a Quantity!'}, namespace='/game', to=game_room.get_sid_from_players(name))
                return
        
            if 'price' not in parsed_data:
                socketio.emit('bid_status', {'message': f'Enter a Price!'}, namespace='/game', to=game_room.get_sid_from_players(name))
                return

            # Check price is valid and dosen't exceed market price
            if (parsed_data_clean["price"] < 0 or parsed_data_clean["price"] > market_cap):
                socketio.emit('bid_status', {'message': 'Enter a different price (ensure it is non-negative number below the market cap)'}, namespace='/game', to=game_room.get_sid_from_players(name))
                return
            
            player_quantity = game_room.get_player_data_object(name).get_all_player_units()
            if (parsed_data_clean["quantity"] < 0 or parsed_data_clean["quantity"] > player_quantity):
                socketio.emit('bid_status', {'message': f'Enter a different quantity (ensure it is non-negative number below the number of units you have ({player_quantity})'}, namespace='/game', to=game_room.get_sid_from_players(name))
                return
            
            game_room.get_player_data_object(name).get_player_single_bid().set_price_quantity(float(parsed_data_clean["price"]), float(parsed_data_clean["quantity"]))
        
        game_room.get_player_data_object(name).set_bid_status(True)
        
        socketio.emit('bid_status', {'message': 'Bid successful!'}, namespace='/game', to=game_room.get_sid_from_players(name))

        marketUnits = 0
        allBid = game_room.has_all_players_bid()
        if allBid:
            marketUnits = game_room.get_total_bid_units()

        # Get list of asset names and the bid prices
        all_bids = game_room.get_json_all_bids()
        asset_names = []
        bid_prices = []
        for bid in all_bids:
            asset_names.append(bid["asset"])
            bid_prices.append(bid["price"])

        print(f"Submit Bid id: {game_room.get_player_data_object(name).get_id()}")
        data = {
            "allBid": allBid,
            "name": name,
            "player_id": game_room.get_player_data_object(name).get_id(),
            "marketUnits": marketUnits,
            "assetNames": asset_names,
            "bidPrices": bid_prices
        }
        print(f"Send data to room: {data}")
        socketio.emit('all_bids_status', data, namespace='/game', to=room)
 
        print(f"{name} submit data: {parsed_data_clean}")

    def on_run_round(self, data):
        print("Run Round")
        
        room = session.get("room")
        name = session.get("name")
        game_room = room_manager.get_room(room)
        dbg("room:", room, "admin:", game_room.get_admin(), "caller:", name)
        dbg("raw payload:", data)

        if game_room.get_admin() != name:
            return

        # Check if everyone has voted
        if not game_room.has_all_players_bid():
            socketio.emit('bid_status', {'message': f'Not everyone has voted!'}, namespace='/game', to=game_room.get_admin_sid())
            return
        
        parsed_data = parse_qs(data.get('data', ''))
        parsed_data_clean = {}

        multi_value_keys = {"assets", "bids"}

        for key, values in parsed_data.items():
            if key in multi_value_keys:
                if key == "bids":
                    # Convert to list of ints
                    parsed_data_clean[key] = [float(v) for v in values]
                else:
                    parsed_data_clean[key] = values  # always a list
            else:
                # single value, try conversions
                val = values[0]
                try:
                    parsed_data_clean[key] = int(val)
                except ValueError:
                    try:
                        parsed_data_clean[key] = float(val)
                    except ValueError:
                        parsed_data_clean[key] = val
        dbg("parsed_data_clean keys:", list(parsed_data_clean.keys()))
        dbg("parsed_data_clean:", parsed_data_clean)
        dbg("slider:", parsed_data_clean.get("slider"), "demand field:", parsed_data_clean.get("demand"))        
        event = parsed_data_clean["event"]

        all_bids = game_room.get_json_all_bids()
        sorted_bids = sorted(all_bids, key=lambda x: (x["price"], x["asset"]))
        dbg("\n--- BIDS (sorted) ---")
        for i, b in enumerate(sorted_bids):
            dbg(f"{i:02d} id={b['id']} player={b['player']} asset={b['asset']} "
                f"price={float(b['price'])} qty={float(b['quantity'])} cost={float(b['generation'])}")
        dbg("--- END BIDS ---\n")

        prices = []
        quantities = []
        for bid in sorted_bids:
            prices.append(bid["price"])
            quantities.append(bid["quantity"])
        demand = parsed_data_clean["slider"]

        #USING QP SOLVER TO AVOID ISSUES WITH LINPROG AND TIES
        # Day-ahead market clearing with QP
        clearing_result = qp_day_ahead_clearing(sorted_bids, demand, lambda_reg=1e-4)

        P_DA = float(clearing_result["market_price"])
        x_DA_VEC = np.asarray(clearing_result["x"], dtype=float)
        x_DA_VEC = allocate_by_price_equal_split(sorted_bids, demand)

        # Always treat IDs as the source of truth across phases
        sorted_bids = clearing_result["bids"]  # may have been re-ordered by the solver wrapper

        # Build x_DA_by_id explicitly from the returned vector + bid list
        x_DA_by_id = {bid["id"]: float(x_DA_VEC[i]) for i, bid in enumerate(sorted_bids)}

        eps_da = clearance_eps(demand)
        # Recompute market price from meaningful cleared quantity (avoid epsilon noise)
        if float(demand) <= eps_da:
            P_DA = 0.0
        else:
            P_DA = compute_market_price_from_clearing(
                sorted_bids,
                x_DA_VEC,
                demand,
                fallback=P_DA
            )

        dbg("\n--- DA CLEARING RESULT ---")
        dbg("EPS_CLEAR (DA):", clearance_eps(demand))
        dbg("P_DA:", P_DA)
        dbg("x_DA_VEC:", x_DA_VEC, "sum(x):", float(np.sum(x_DA_VEC)), "demand:", float(demand))

        # Show row-by-row alignment (this catches phantom marginal + misalignment)
        for i, b in enumerate(sorted_bids):
            dbg(f"{i:02d} id={b['id']} price={float(b['price'])} qty={float(b['quantity'])} x={float(x_DA_VEC[i])}")

        # Cleared IDs using your economic epsilon
        cleared_ids = [sorted_bids[i]["id"] for i in range(len(sorted_bids)) if float(x_DA_VEC[i]) > eps_da]
        dbg("cleared_ids:", cleared_ids)
        dbg("--- END DA CLEARING ---\n")


        graphData = linprog_to_graph(sorted_bids, x_DA_VEC, demand, P_DA)
        dbg("\n--- GRAPH DATA (DA) ---")
        dbg("EPS_CLEAR (DA):", clearance_eps(demand))
        dbg("graph demand:", graphData["demand"], "graph marketPrice:", graphData["marketPrice"])
        dbg("bars:", len(graphData["widthBar"]), "sum(widthBar):", float(sum(graphData["widthBar"])))
        dbg("--- END GRAPH DATA (DA) ---\n")
        # --- DA SETTLEMENT (persist profits exactly once) ---
        da_per_bid, da_per_player = settle_day_ahead(
            bids=sorted_bids,
            P_DA=P_DA,
            x_DA_by_id=x_DA_by_id
        )
        dbg("\n--- DA SETTLEMENT ---")
        dbg("da_per_bid sample:", da_per_bid[:3])
        dbg("da_per_player:", sorted(da_per_player, key=lambda x: x["gain"], reverse=True))
        dbg("--- END DA SETTLEMENT ---\n")

        # Leaderboards / UI ordering
        sorted_player_gains_before_event = sorted(da_per_player, key=lambda x: x["gain"], reverse=True)
        sorted_round_da = sorted(da_per_player, key=lambda x: x["gain"], reverse=True)

        # Cumulative profit table (post-DA settlement)
        player_profits_report = [
            {"player": b["player"], "id": b["id"], "total": float(b["data"].get_profit())}
            for b in sorted_bids
        ]
        sorted_player_profits = sorted(player_profits_report, key=lambda x: x["total"], reverse=True)

        # sorted_player_gains_post_event
        data_before_event =  {
                    "graphData": graphData,
                    "playerProfits": sorted_player_profits,
                    "playerGainsBeforeEvent": sorted_player_gains_before_event,
                    "playerRoundDA": sorted_round_da,
                    "roundNumber": game_room.get_current_round() + 1,
                    "P_DA": P_DA,

                    "x_DA_by_id": x_DA_by_id,
                    "daPerBid": da_per_bid
                }
        
        print(f"the marketUnits: {parsed_data_clean['marketUnits']}")

        # get list of assets and bids selected
        selected_assets = parsed_data_clean.get('assets', [])
        selected_bids = parsed_data_clean.get('bids', [])
        event_demand_adjust = parsed_data_clean.get('eventDemandAdjust', 0)
        print(f"selected bids: {selected_bids}")

        data_after_event = self.rand_event(
            sorted_bids=sorted_bids,
            demand=demand,
            event=event,
            marketUnits=parsed_data_clean["marketUnits"],
            P_DA=P_DA,
            x_DA_by_id=x_DA_by_id,
            da_per_bid=da_per_bid,
            selected_assets=selected_assets,
            selected_bids=selected_bids,
            event_demand_adjust=event_demand_adjust
        )


        data = data_before_event | data_after_event

        
        socketio.emit('round_over', data, namespace='/game', to=room)
  

        game_room.set_all_players_bid_status(False)
        game_room.increment_round()

    def rand_event(self, sorted_bids, demand, event, marketUnits, P_DA, x_DA_by_id, da_per_bid, selected_assets, selected_bids, event_demand_adjust = 0):
        dbg("\n================= RAND EVENT =================")
        dbg("event:", event, "P_DA:", P_DA, "demand_in:", float(demand))
        dbg("x_DA_by_id:", x_DA_by_id)

        event_bids, demand_new, meta = apply_event_to_bids(
            sorted_bids=sorted_bids,
            demand=demand,
            event=event,
            marketUnits=marketUnits,
            market_price_DA=P_DA,
            selected_assets=selected_assets,
            selected_bids=selected_bids,
            event_demand_adjust=event_demand_adjust,
            x_DA_by_id=x_DA_by_id
        )
        dbg("\n--- APPLY EVENT OUTPUT ---")
        dbg("meta:", meta)
        dbg("demand_new:", float(demand_new), "delta:", float(demand_new) - float(demand))
        dbg("num_bids before:", len(sorted_bids), "after:", len(event_bids))

        # Show which IDs exist now (catch: removed bidder still referenced later)
        dbg("event_bids ids:", [b["id"] for b in event_bids])

        # If your meta has removed_ids / penalized_ids:
        dbg("removed_ids:", meta.get("removed_ids"))
        dbg("penalized_ids:", meta.get("penalized_ids"))
        dbg("--- END APPLY EVENT ---\n")

        event_name = meta.get("event_name", "None")
        event_bids = sorted(event_bids, key=lambda b: (float(b["price"]), str(b["asset"])))

        rt = solve_real_time_dispatch_qp(
            event_bids=event_bids,
            demand_new=demand_new,
            x_DA_by_id=x_DA_by_id,
            lambda_reg=1e-4
        )
        dbg("\n--- RT SOLVE ---")
        dbg("EPS_CLEAR (RT):", clearance_eps(demand_new))
        dbg("rt status:", rt.get("status"))
        dbg("P_RT raw:", rt.get("P_RT"), "delta_D:", rt.get("delta_D"))

        y_by_id = rt["y_by_id"]
        x_rt_by_id = rt["x_rt_by_id"]

        dbg("sum(y):", float(sum(y_by_id.values())))
        dbg("sum(x_RT):", float(sum(x_rt_by_id.values())))
        dbg("--- END RT SOLVE ---\n")

        y_by_id = rt["y_by_id"]
        x_rt_by_id = rt["x_rt_by_id"]
        P_RT = rt["P_RT"]
        delta_D = rt["delta_D"]

        if P_RT is None:
            P_RT = float(P_DA)

        x_rt_vec = np.array(
            [float(x_rt_by_id.get(b["id"], 0.0)) for b in event_bids],
            dtype=float
        )
        x_rt_vec = allocate_by_price_equal_split(event_bids, demand_new)
        eps_rt = clearance_eps(demand_new)
        x_rt_vec = apply_clearance_threshold(x_rt_vec, demand_new)
        x_rt_by_id = {b["id"]: float(x_rt_vec[i]) for i, b in enumerate(event_bids)}
        # If not cleared in RT, zero out deviation so RT profit is zero.
        for bid in event_bids:
            bid_id = bid["id"]
            if float(x_rt_by_id.get(bid_id, 0.0)) < eps_rt:
                y_by_id[bid_id] = 0.0
        P_RT = compute_market_price_from_clearing(
            event_bids,
            x_rt_vec,
            demand_new,
            fallback=P_RT
        )

        # Prepare data for after-event reporting
        sorted_bids_for_graph = event_bids
        x_for_graph = np.array([float(x_rt_by_id.get(b["id"], 0.0)) for b in sorted_bids_for_graph], dtype=float)
        graphData = linprog_to_graph(sorted_bids_for_graph, x_for_graph, float(demand_new), float(P_RT))

        dbg("\n--- GRAPH DATA (AE) ---")
        dbg("graph demand:", graphData["demand"], "graph marketPrice:", graphData["marketPrice"])
        dbg("bars:", len(graphData["widthBar"]), "sum(widthBar):", float(sum(graphData["widthBar"])))
        dbg("--- END GRAPH DATA (AE) ---\n")


        # --- RT FULL SETTLEMENT (re-clearing market) ---
        rt_full_per_bid, rt_full_per_player = compute_real_time_full(
            bids=event_bids,
            P_RT=P_RT,
            x_rt_by_id=x_rt_by_id,
            penalized_ids=meta.get("penalized_ids", []),
            penalized_settlement_price=1.0
        )

        rt_full_by_id = {b["id"]: float(b.get("gain_RT_full", 0.0)) for b in rt_full_per_bid}
        da_gain_by_bid_id = {b["id"]: float(b.get("gain_DA", 0.0)) for b in (da_per_bid or [])}
        rt_delta_per_bid = []
        rt_delta_by_player = {}

        # Replace DA with RT (delta = RT_full - DA)
        for b in sorted_bids:
            bid_id = b["id"]
            da_gain = float(da_gain_by_bid_id.get(bid_id, 0.0))
            rt_full_gain = float(rt_full_by_id.get(bid_id, 0.0))
            delta = rt_full_gain - da_gain

            if abs(delta) < 1e-12:
                delta = 0.0

            b["data"].add_to_profit(delta)

            rt_delta_per_bid.append({
                "player": b["player"],
                "id": bid_id,
                "gain_RT": delta,
                "x_RT": float(x_rt_by_id.get(bid_id, 0.0)),
                "mc": float(b["generation"]),
            })

            rt_delta_by_player[b["player"]] = rt_delta_by_player.get(b["player"], 0.0) + delta

        rt_per_bid = rt_delta_per_bid
        rt_per_player = [{"player": p, "gain": g} for p, g in rt_delta_by_player.items()]

        # Note: removed bidders already get net-zero per round (delta = -DA).

        # Cumulative profit table AFTER RT settlement (include all original bids)
        player_profits = [
            {"player": b["player"], "id": b["id"], "total": float(b["data"].get_profit())}
            for b in sorted_bids
        ]
        dbg("\n--- RT SETTLEMENT ---")
        dbg("rt_per_bid sample:", rt_per_bid[:3])
        dbg("rt_per_player:", sorted(rt_per_player, key=lambda x: x["gain"], reverse=True))
        dbg("profit totals sample:", player_profits[:3])
        dbg("--- END RT SETTLEMENT ---\n")
        
        # RT gains this event/phase (total RT market gains)
        player_gains = rt_full_per_player
        
        sorted_player_profits = sorted(player_profits, key=lambda x: x["total"], reverse=True)
        sorted_player_gains_after_event = sorted(player_gains, key=lambda x: x["gain"], reverse=True)
        sorted_round_rt = sorted(rt_full_per_player, key=lambda x: x["gain"], reverse=True)

        data = {
            "event": {"event_name": event_name, "event_tag": event},
            "graphDataAE": graphData,             # after-event dispatch graph
            "playerProfitsAE": sorted_player_profits,
            "playerGainsAE": sorted_player_gains_after_event,
            "playerRoundRT": sorted_round_rt,
            "removedIds": list(meta.get("removed_ids") or []),

            "P_RT": float(P_RT),
            "delta_D": float(delta_D),
            "y_by_id": y_by_id,
            "x_rt_by_id": x_rt_by_id,
            "x_RT_by_id": x_rt_by_id,
            "rtPerBid": rt_per_bid,
            "eventMeta": meta,
        }

        return data
    
    @socketio.on('change_phase', namespace='/game')
    def handle_change_phase(data):
        phase = data.get('phase')
        if phase is not None:
            print(f"[SERVER] Received change_phase to: {phase}")
            # Broadcast to all clients in the namespace
            emit('update_phase', {'phase': phase}, namespace='/game', broadcast=True)

socketio.on_namespace(LobbyNamespace('/lobby'))
socketio.on_namespace(GameNamespace('/game'))

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5001, debug=True)
