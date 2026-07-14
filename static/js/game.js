import { io } from "https://cdn.socket.io/4.8.1/socket.io.esm.min.js";

$(document).ready(function() {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const socket = io("/game", {
        auth: { csrf_token: csrfToken }
    });

    let currentPhase = 0;
    let data_for_graph;
    let profits_gains = {};
    let sharedXRange = null;
    const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    }[char]));
    const formatMoney = (value) => {
        const num = Number(value);
        const safeNum = Number.isFinite(num) ? num : 0;
        return safeNum.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
    };

    socket.emit('get_stats');

    socket.on('send_stats', (data) => {
        $('#round').text(data["currentRound"]);
        const assets = data["bids"].map(a => `
            <div class="asset-card">
                <div class="asset-type">${escapeHtml(a['asset'])}</div>
                <div class="asset-detail">
                    Capacity: ${a['units']} MW<br>
                    Cost: $${a['generation']} / MWh
                </div>
            </div>
        `).join("");
        $('#assets-list').html(assets);
    });

    $('#leave-btn').on("click", () => { document.querySelector('#logout-form')?.requestSubmit(); });

    $('#bid-form').submit((e) => {
        e.preventDefault();
        socket.emit('submit_bid', { data: $('#bid-form').serialize() });
        $('#bid-form')[0].reset();
    });

    $('#default-form').submit((e) => {
        e.preventDefault();
        socket.emit('submit_bid', { data: $('#default-form').serialize() });
        $('#bid-form')[0].reset();
    });

    $('#nextGraphBtn').on('click', () => {
        currentPhase = (currentPhase + 1) % 2;
        socket.emit('change_phase', { phase: currentPhase });
    });

    $('#startNextRound').on('click', () => { socket.emit('start_next_round'); });

    socket.on('round_over', (data) => {
        $('#errMsg').empty();
        const profitsById = {};
        data["playerProfits"].forEach(p => { profitsById[p["id"]] = { name: p["player"], before: p["total"], after: null }; });
        data["playerProfitsAE"].forEach(p => {
            if (!profitsById[p["id"]]) profitsById[p["id"]] = { name: p["player"], before: null, after: p["total"] };
            else profitsById[p["id"]].after = p["total"];
        });

        const roundDAByPlayer = {};
        (data["playerRoundDA"] || []).forEach(p => { roundDAByPlayer[p["player"]] = Number(p["gain"]) || 0; });
        const roundRTByPlayer = {};
        (data["playerRoundRT"] || []).forEach(p => { roundRTByPlayer[p["player"]] = Number(p["gain"]) || 0; });
        const removedIds = new Set(data["removedIds"] || []);

        const profitRowsDA = Object.entries(profitsById).map(([id, info]) => {
            const roundDA = roundDAByPlayer[info.name] ?? 0;
            return `<tr id="${id}" class="bid-unready">
                <td data-label="Player">${escapeHtml(info.name)}</td>
                <td data-label="DA">$${formatMoney(roundDA)}</td>
                <td data-label="RT">$0</td>
                <td data-label="Total">$${formatMoney(roundDA)}</td>
            </tr>`;
        }).join("");

        const profitRowsRT = Object.entries(profitsById).map(([id, info]) => {
            const roundDA = roundDAByPlayer[info.name] ?? 0;
            const roundRT = removedIds.has(id) ? 0 : (roundRTByPlayer[info.name] ?? 0);
            const change = roundRT - roundDA;
            const changeClass = change > 0 ? "positive" : (change < 0 ? "negative" : "");
            return `<tr id="${id}" class="bid-unready">
                <td data-label="Player">${escapeHtml(info.name)}</td>
                <td data-label="DA">$${formatMoney(roundDA)}</td>
                <td data-label="RT"><span class="${changeClass}">$${formatMoney(change)}</span></td>
                <td data-label="Total">$${formatMoney(roundRT)}</td>
            </tr>`;
        }).join("");

        const cumulativeBeforeByPlayer = {};
        (data["playerProfits"] || []).forEach(p => { cumulativeBeforeByPlayer[p["player"]] = Number(p["total"]) || 0; });
        const cumulativeAfterByPlayer = {};
        (data["playerProfitsAE"] || []).forEach(p => { cumulativeAfterByPlayer[p["player"]] = Number(p["total"]) || 0; });

        const gains = Object.entries(profitsById).map(([id, info]) => {
            const total = cumulativeBeforeByPlayer[info.name] ?? 0;
            const color = total > 0 ? "positive" : (total < 0 ? "negative" : "");
            return `<li>${escapeHtml(info.name)}: <span class="${color}">$${formatMoney(total)}</span></li>`;
        }).join("");

        const gains_AE = Object.entries(profitsById).map(([id, info]) => {
            const total = cumulativeAfterByPlayer[info.name] ?? 0;
            const color = total > 0 ? "positive" : (total < 0 ? "negative" : "");
            return `<li>${escapeHtml(info.name)}: <span class="${color}">$${formatMoney(total)}</span></li>`;
        }).join("");

        // Show DA view first — RT reveals when phase switches to 1
        $('#playerProfitTableBody').html(profitRowsDA);
        $('#playerGains').html(gains);
        $('#round').text(data["roundNumber"]);
        $('#form-submit').html("<h1>Waiting for all bids…</h1>");

        currentPhase = 0;
        data_for_graph = data;
        profits_gains["profits_table_DA"] = profitRowsDA;
        profits_gains["profits_table_RT"] = profitRowsRT;
        profits_gains["gains"] = gains;
        profits_gains["gains_AE"] = gains_AE;

        sharedXRange = null;
        updateGraph(data, currentPhase);
    });

    socket.on('update_phase', (data) => {
        currentPhase = data.phase;
        updateLeader(profits_gains, currentPhase);
        updateGraph(data_for_graph, currentPhase);

    });

    socket.on('bid_status', (data) => {
        $('#errMsg').empty().append($('<p>').text(data.message));
    });

    socket.on('all_bids_status', (data) => {
        const player = $(`#${data["player_id"]}`);
        if (player.hasClass("bid-unready")) player.removeClass("bid-unready").addClass("bid-ready");

        if (data["allBid"]) {
            // Cap demand event slider at 500 MW or market size, whichever is smaller
            const demandAdjustMax = Math.min(500, data["marketUnits"]);
            const demandAdjustDefault = Math.trunc(demandAdjustMax / 2);
            const form = `
            <form method="POST" id="round-form">
                <input type="hidden" name="csrf_token" value="${escapeHtml(csrfToken)}">
                <label for="slider">Slider:</label>
                <input type="range" id="slider" name="slider" min="0" max="${data["marketUnits"]}" value="${Math.trunc(data["marketUnits"] / 2)}">
                <label for="demand">Demand:</label>
                <input type="number" id="demand" name="demand" min="0" max="${data["marketUnits"]}" value="${Math.trunc(data["marketUnits"] / 2)}">

                <p>Select an event:</p>
                <label><input type="radio" name="event" value="high_dem"> Higher Demand</label><br>
                <label><input type="radio" name="event" value="low_dem" checked> Lower Demand</label><br>
                <label><input type="radio" name="event" value="high_bidder_remove"> Remove Highest Cleared Bidder</label><br>
                <label><input type="radio" name="event" value="low_bidder_remove"> Remove Lowest Cleared Bidder</label><br>
                <label><input type="radio" name="event" value="tax_coal&nat_gas"> Tax on Coal and Natural Gas</label><br>
                <label><input type="radio" name="event" value="remove_renewable"> Remove Renewable Generators</label><br>
                <label><input type="radio" name="event" value="renewable_subsidies"> Renewable Subsidies</label><br>
                <label><input type="radio" name="event" value="remove_by_asset_name"> Remove Generators by Asset Name</label><br>
                <label><input type="radio" name="event" value="remove_by_bid_price"> Remove Generators by Bid Price</label><br>
                <label><input type="radio" name="event" value="penalty_high_bid"> Regulator Intervention</label><br>
                <label><input type="radio" name="event" value="pay_as_bid"> Pay As Bid</label><br>
                <label><input type="radio" name="event" value="none"> None</label><br>

                <div id="assetDropdownContainer" style="display:none; margin-top:10px;"></div>
                <div id="demandEventContainer" style="display:none; margin-top:10px;">
                    <p id="demandEventLabel">Demand change (MW):</p>
                    <input type="range" id="eventDemandAdjust" name="eventDemandAdjust" min="0" max="${demandAdjustMax}" value="${demandAdjustDefault}">
                    <span id="eventDemandAdjustValue">${demandAdjustDefault}</span> MW
                </div>

                <input type="hidden" name="marketUnits" value="${data["marketUnits"]}">
                <input type="submit" id="submit" name="round-submit" value="Run Round">
            </form>`;
            $('#form-submit').html(form);

            const $dropdownContainer = $('#assetDropdownContainer');
            const $demandEventContainer = $('#demandEventContainer');
            const $demandEventLabel = $('#demandEventLabel');
            const $eventDemandAdjustValue = $('#eventDemandAdjustValue');

            $('#round-form').on('change', 'input[name="event"]', function () {
                const selectedValue = $(this).val();

                if (selectedValue === 'remove_by_asset_name') {
                    $dropdownContainer.empty().show();
                    const $label = $('<p>').text('Select assets:');
                    $dropdownContainer.append($label);
                    $.each(data['assetNames'], function (i, asset) {
                        const $cb = $('<input>').attr({ type: 'checkbox', name: 'assets', value: asset, id: `asset-${i}` });
                        const $lbl = $('<label>').attr('for', `asset-${i}`).text(asset);
                        $dropdownContainer.append($cb).append($lbl).append('<br>');
                    });
                } else if (selectedValue === 'remove_by_bid_price') {
                    $dropdownContainer.empty().show();
                    const $label = $('<p>').text('Select prices:');
                    $dropdownContainer.append($label);
                    $.each(data['bidPrices'], function (i, bid) {
                        const $cb = $('<input>').attr({ type: 'checkbox', name: 'bids', value: bid, id: `bid-${i}` });
                        const $lbl = $('<label>').attr('for', `bid-${i}`).text(bid);
                        $dropdownContainer.append($cb).append($lbl).append('<br>');
                    });
                } else {
                    $dropdownContainer.hide().empty();
                }

                if (selectedValue === 'high_dem') {
                    $demandEventLabel.text('Increase demand by (MW):');
                    $demandEventContainer.show();
                } else if (selectedValue === 'low_dem') {
                    $demandEventLabel.text('Decrease demand by (MW):');
                    $demandEventContainer.show();
                } else {
                    $demandEventContainer.hide();
                }
            });

            $('#round-form').on('input', '#eventDemandAdjust', function () {
                $eventDemandAdjustValue.text($(this).val());
            });
        }
    });

    $(document).on('submit', '#round-form', (e) => {
        e.preventDefault();
        socket.emit('run_round', { data: $('#round-form').serialize() });
    });

    $(document).on("input", "#slider", function () { $("#demand").val($(this).val()); });
    $(document).on("input", "#demand", function () {
        let value = parseInt($(this).val(), 10);
        const min = parseInt($(this).attr("min"), 10);
        const max = parseInt($(this).attr("max"), 10);
        if (value < min) $(this).val(min);
        if (value > max) $(this).val(max);
        $("#slider").val($(this).val());
    });


    function updateLeader(profits_gains, phase) {
        if (phase === 0) {
            $('#playerProfitTableBody').html(profits_gains["profits_table_DA"]);
            $('#playerGains').html(profits_gains["gains"]);
        } else if (phase === 1) {
            $('#playerProfitTableBody').html(profits_gains["profits_table_RT"]);
            $('#playerGains').html(profits_gains["gains_AE"]);
        } else {
            console.log("improper phase");
        }
    }

    function updateGraph(data, phase) {
        const config = { displayModeBar: false, displaylogo: false, scrollZoom: false, staticPlot: false, editable: false };

        if (phase === 0) {
            const in_data = data["graphData"];
            const graph = document.querySelector('.bidGraph');
            const { demand, marketPrice, xList, widthBar, barHeight, colors, players, costs, assets } = in_data;
            const roundNumber = data["roundNumber"];
            const useLinearYAxis = [...barHeight, ...costs].some(v => Number(v) < 0);

            if (!sharedXRange) {
                const totalWidth = widthBar.reduce((acc, w) => acc + w, 0);
                const maxX = Math.max(totalWidth, demand);
                sharedXRange = [0, Math.ceil(maxX / 10) * 10 + 200];
            }

            const shapes = [
                { type: "line", x0: 0, x1: Math.max(widthBar.reduce((a,c) => a+c, 0), demand), y0: marketPrice, y1: marketPrice, line: { color: "red", width: 3, dash: "dash" } },
                { type: "line", x0: demand, x1: demand, y0: useLinearYAxis ? Math.min(...costs, ...barHeight, 0) - 10 : 0, y1: useLinearYAxis ? Math.max(...barHeight, marketPrice, ...costs) + 25 : 100000, line: { color: "black", width: 3, dash: "dash" } }
            ];
            let cx = 0;
            for (let i = 0; i < xList.length; i++) {
                const w = widthBar[i], c = costs[i], center = cx + w/2;
                shapes.push({ type:'line', x0: center-w/2, x1: center+w/2, y0: c, y1: c, line: { color:'blue', width:2, dash:'solid' } });
                cx += w;
            }

            const linMin = Math.min(...costs, ...barHeight, 0) - 10;
            const linMax = Math.max(...barHeight, marketPrice, ...costs) + 25;

            Plotly.newPlot(graph, [{
                type:'bar', x:xList, y:barHeight, width:widthBar, name:"Bids Before the Event",
                marker:{color:colors},
                hovertext: widthBar.map((w,i) => `<b>${escapeHtml(players[i])}</b><br>Asset: ${escapeHtml(assets[i])}<br>Quantity: ${w}<br>Price: ${barHeight[i]}`),
                hoverinfo:"text"
            }], {
                barmode:'overlay',
                title:{ text:`Electricity Market Round ${roundNumber - 1} (without event)` },
                xaxis:{ title:{ text:'Quantity (MW)' }, range: sharedXRange },
                yaxis:{ title:{ text:'Price ($/MWh)' }, type: useLinearYAxis?'linear':'log', range: useLinearYAxis?[linMin,linMax]:[0,5], tickmode: useLinearYAxis?'auto':'array', tickvals: useLinearYAxis?undefined:[1,10,100,1000,10000], ticktext: useLinearYAxis?undefined:['1','10','100','1000','10000'] },
                dragmode:false, shapes,
                annotations:[
                    { xref:"paper", yref:"paper", x:0.01, y:0.99, xanchor:"left", yanchor:"top", text:`Market Price: ${marketPrice}`, showarrow:false, bgcolor:"rgba(255,255,255,0.85)", bordercolor:"red", borderwidth:1, font:{color:"red",size:14} },
                    { x:demand, y: useLinearYAxis?linMax:Math.log10(10000), xanchor:"right", yanchor:"bottom", text:`Demand: ${demand}`, showarrow:true, arrowcolor:"black", ax:-20, ay:-10, font:{color:"black",size:14} }
                ]
            }, config);

        } else if (phase === 1) {
            const in_data_AE = data["graphDataAE"];
            const graph = document.querySelector('.bidGraph');
            const { xList:xList_AE, costs:costs_AE, widthBar:widthBar_AE, barHeight:barHeight_AE, players:players_AE, colors:colors_AE, marketPrice:marketPrice_AE, demand:demand_AE, assets:assets_AE } = in_data_AE;
            const roundNumber = data["roundNumber"];
            const { event_name, event_tag } = data["event"];
            const useLinearYAxis = event_tag === "renewable_subsidies" || [...barHeight_AE, ...costs_AE].some(v => Number(v) < 0);

            const shapes = [
                { type:"line", x0:0, x1:Math.max(widthBar_AE.reduce((a,c)=>a+c,0), demand_AE), y0:marketPrice_AE, y1:marketPrice_AE, line:{color:"red",width:3,dash:"dash"} },
                { type:"line", x0:demand_AE, x1:demand_AE, y0: useLinearYAxis?Math.min(...costs_AE,0)-10:0, y1: useLinearYAxis?Math.max(...barHeight_AE,marketPrice_AE,...costs_AE)+25:100000, line:{color:"black",width:3,dash:"dash"} }
            ];
            let cx = 0;
            for (let i = 0; i < xList_AE.length; i++) {
                const w = widthBar_AE[i], c = costs_AE[i], center = cx + w/2;
                shapes.push({ type:'line', x0:center-w/2, x1:center+w/2, y0:c, y1:c, line:{color:'blue',width:2,dash:'solid'} });
                cx += w;
            }

            const linMin = Math.min(...costs_AE, 0) - 10;
            const linMax = Math.max(...barHeight_AE, marketPrice_AE, ...costs_AE) + 25;

            Plotly.newPlot(graph, [{
                type:'bar', x:xList_AE, y:barHeight_AE, width:widthBar_AE, name:"Bids After the Event",
                marker:{color:colors_AE},
                hovertext: widthBar_AE.map((w,i) => `<b>${escapeHtml(players_AE[i])}</b><br>Asset: ${escapeHtml(assets_AE[i])}<br>Quantity: ${w}<br>Price: ${barHeight_AE[i]}`),
                hoverinfo:"text"
            }], {
                barmode:'overlay',
                title:{ text:`Electricity Market Round ${roundNumber - 1} (${escapeHtml(event_name)})` },
                xaxis:{ title:{ text:'Quantity (MW)' }, range: sharedXRange },
                yaxis:{ title:{ text:'Price ($/MWh)' }, type: useLinearYAxis?'linear':'log', range: useLinearYAxis?[linMin,linMax]:[0,5], tickmode: useLinearYAxis?'auto':'array', tickvals: useLinearYAxis?undefined:[1,10,100,1000,10000], ticktext: useLinearYAxis?undefined:['1','10','100','1000','10000'] },
                dragmode:false, shapes,
                annotations:[
                    { xref:"paper", yref:"paper", x:0.01, y:0.99, xanchor:"left", yanchor:"top", text:`Market Price: ${marketPrice_AE}`, showarrow:false, bgcolor:"rgba(255,255,255,0.85)", bordercolor:"red", borderwidth:1, font:{color:"red",size:14} },
                    { x:demand_AE, y: useLinearYAxis?linMax:Math.log10(10000), xanchor:"right", yanchor:"bottom", text:`Demand: ${demand_AE}`, showarrow:true, arrowcolor:"black", ax:-20, ay:-10, font:{color:"black",size:14} }
                ]
            }, config);
        } else {
            console.log("IMPROPER PHASE");
        }
    }
});
