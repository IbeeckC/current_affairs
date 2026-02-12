import { io } from "https://cdn.socket.io/4.8.1/socket.io.esm.min.js";

$(document).ready(function() {

    const socket = io("/game");

     let currentPhase = 0;
     let data_for_graph;
     let profits_gains = {};
     let sharedXRange = null;
     const formatMoney = (value) => {
         const num = Number(value);
         const safeNum = Number.isFinite(num) ? num : 0;
         return safeNum.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
     };

    // Get Initial Game Info
    socket.emit('get_stats');

    socket.on('send_stats', (data) => {
        $('#round').html(data["currentRound"]);
        const assets = data["bids"].map(a => {
            return `
                <p><b>Asset Type:</b> ${a['asset']}</p>
                <p><b>Generation Capacity:</b> ${a['units']} MW</p>
                <p><b>Generation Cost:</b> $${a['generation']} / MWh</p>
                <br>
            `
        });
        $('#assets-list').html(assets);
    });

    $('#leave-btn').on("click", () => {
        location.href = "/logout";
    });

    $('#bid-form').submit((e) => {
        e.preventDefault();
        const formData = $('#bid-form').serialize();
        socket.emit('submit_bid', { data: formData });
        $('#bid-form')[0].reset();
    });

    $('#default-form').submit((e) => {
        e.preventDefault();
        const formData = $('#default-form').serialize();
        socket.emit('submit_bid', { data: formData });
        $('#bid-form')[0].reset();
    });

    $('#nextGraphBtn').on('click', () => {
        console.log("Next Button Clicked");
        currentPhase = (currentPhase + 1) % 2;
        socket.emit('change_phase', { phase: currentPhase });
    });

    $('#startNextRound').on('click', () => {
        console.log("Start Next Round Button Clicked");
        socket.emit('start_next_round');
    });


    socket.on('round_over', (data) => {
        $('#errMsg').empty();
        const profitsById = {};
        data["playerProfits"].forEach(p => {
            profitsById[p["id"]] = { name: p["player"], before: p["total"], after: null };
        });
        data["playerProfitsAE"].forEach(p => {
            if (!profitsById[p["id"]]) {
                profitsById[p["id"]] = { name: p["player"], before: null, after: p["total"] };
            } else {
                profitsById[p["id"]].after = p["total"];
            }
        });

        const roundDAByPlayer = {};
        (data["playerRoundDA"] || []).forEach(p => {
            roundDAByPlayer[p["player"]] = Number(p["gain"]) || 0;
        });
        const roundRTByPlayer = {};
        (data["playerRoundRT"] || []).forEach(p => {
            roundRTByPlayer[p["player"]] = Number(p["gain"]) || 0;
        });
        const removedIds = new Set(data["removedIds"] || []);

        const profitRows = Object.entries(profitsById).map(([id, info]) => {
            const roundDA = roundDAByPlayer[info.name] ?? 0;
            const roundRT = removedIds.has(id) ? 0 : (roundRTByPlayer[info.name] ?? 0);
            const change = roundRT - roundDA;
            const changeClass = change > 0 ? "positive" : (change < 0 ? "negative" : "");
            return `
                <tr id="${id}" class="bid-unready">
                    <td>${info.name}</td>
                    <td>$${formatMoney(roundDA)}</td>
                    <td><span class="${changeClass}">$${formatMoney(change)}</span></td>
                    <td>$${formatMoney(roundRT)}</td>
                </tr>
            `;
        }).join("");

        const gainsBeforeList = data["playerProfits"];
        const gainsAfterList = data["playerProfitsAE"];

        const gains = gainsBeforeList.map(g => {
            let color = ""
            if (g["total"] > 0) {
                color = "positive"
            } else if (g["total"] < 0) {
                color = "negative"
            }
            return `<li>${g["player"]}: <span class=${color}>$${formatMoney(g["total"])}</span></li>`
        }).join("");

        const gains_AE = gainsAfterList.map(g => {
            let color = ""
            if (g["total"] > 0) {
                color = "positive"
            } else if (g["total"] < 0) {
                color = "negative"
            }
            return `<li>${g["player"]}: <span class=${color}>$${formatMoney(g["total"])}</span></li>`
        }).join("");

        $('#playerProfitTableBody').html(profitRows);
        $('#playerGains').html(gains);
        $('#round').html(data["roundNumber"]);
        $('#form-submit').html("<h1>Waiting for all bids...</h1>");

        currentPhase = 0;
        data_for_graph = data;
        profits_gains["profits_table"] = profitRows;
        profits_gains["gains"] = gains;
        profits_gains["gains_AE"] = gains_AE;
        
        sharedXRange = null;
        updateGraph(data, currentPhase); 
        
        console.log("ROUND_OVER keys:", Object.keys(data));
        console.log("DA price/demand:", data.P_DA, data.graphData?.demand, data.graphData?.marketPrice);
        console.log("AE price/demand:", data.P_RT, data.graphDataAE?.demand, data.graphDataAE?.marketPrice);
        console.log("profits:", data.playerProfits);
        console.log("profitsAE:", data.playerProfitsAE);

    });

    socket.on('update_phase', (data) => {
        currentPhase = data.phase;
        updateLeader(profits_gains, currentPhase);
        updateGraph(data_for_graph, currentPhase); // Update the local graph
    });

    socket.on('bid_status', (data) => {
        $('#errMsg').html(`<p>${data.message}</p>`);
    });

    socket.on('all_bids_status', (data) => {
        console.log(`Received Data: ${data["allBid"]}`)
        const player = $(`#${data["player_id"]}`);
        if (player.hasClass("bid-unready")) {
            player.removeClass("bid-unready").addClass("bid-ready");
        }

        if (data["allBid"]) {
        const halfUnits = Math.trunc(data["marketUnits"] / 2);
        const quarterUnits = Math.trunc(data["marketUnits"] / 4);
        const form = `
            <form method="POST" id="round-form">
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
                <label><input type="radio" name="event" value="remove_by_asset_name"> Remove Generators by Asset Name</label><br>
                <label><input type="radio" name="event" value="remove_by_bid_price"> Remove Generators by Bid Price</label><br>
                <label><input type="radio" name="event" value="penalty_high_bid"> Regulator Intervention</label><br>
                <label><input type="radio" name="event" value="none"> None </label><br>

                <div id="assetDropdownContainer" style="display:none; margin-top:10px;"></div>
                <div id="demandEventContainer" style="display:none; margin-top:10px;">
                    <p id="demandEventLabel">Demand change (MW):</p>
                    <input type="range"
                        id="eventDemandAdjust"
                        name="eventDemandAdjust"
                        min="0"
                        max="${halfUnits}"
                        value="${quarterUnits}">
                    <span id="eventDemandAdjustValue">${quarterUnits}</span> MW
                </div>

                <input type="hidden" name="marketUnits" value="${data["marketUnits"]}">

                <input type="submit" id="submit" name="round-submit" value="Run Round">
            </form>
            `;
            $('#form-submit').html(form);

            // === DROPDOWN LOGIC HERE ===

            // Your assetList array - replace with your actual data if available:
            const assetList = data['assetNames'];
            const bidList = data['bidPrices'];

            // Cache the container
            const $dropdownContainer = $('#assetDropdownContainer');
            const $demandEventContainer = $('#demandEventContainer');
            const $demandEventLabel = $('#demandEventLabel');
            const $eventDemandAdjust = $('#eventDemandAdjust');
            const $eventDemandAdjustValue = $('#eventDemandAdjustValue');

            // Attach event listener to radio buttons within the newly inserted form
            $('#round-form').on('change', 'input[name="event"]', function () {
                const selectedValue = $(this).val();

                if (selectedValue === 'remove_by_asset_name') {
                    $dropdownContainer.empty().show();
                    const assetList = data['assetNames'];
                    const $label = $('<p>').text('Select assets:');
                    $dropdownContainer.append($label);

                    $.each(assetList, function (i, asset) {
                        const $checkbox = $('<input>')
                            .attr({
                                type: 'checkbox',
                                name: 'assets',
                                value: asset,
                                id: `asset-${i}`
                            });

                        const $checkboxLabel = $('<label>')
                            .attr('for', `asset-${i}`)
                            .text(asset);

                        $dropdownContainer
                            .append($checkbox)
                            .append($checkboxLabel)
                            .append('<br>');
                    });

                    $dropdownContainer.append($label).append('<br>').append($select);
                }
                else if (selectedValue === 'remove_by_bid_price') {
                    $dropdownContainer.empty().show();
                    const bidList = data['bidPrices'];
                    const $label = $('<p>').text('Select prices:');
                    $dropdownContainer.append($label);

                    $.each(bidList, function (i, bid) {
                        const $checkbox = $('<input>')
                            .attr({
                                type: 'checkbox',
                                name: 'bids',
                                value: bid,
                                id: `bid-${i}`
                            });

                        const $checkboxLabel = $('<label>')
                            .attr('for', `bid-${i}`)
                            .text(bid);

                        $dropdownContainer
                            .append($checkbox)
                            .append($checkboxLabel)
                            .append('<br>');
                    });

                    $dropdownContainer.append($label).append('<br>').append($select);
                }
                else {
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

    // Admin Functionality

    // Run Round

    $(document).on('submit', '#round-form', (e) => {
        e.preventDefault();
        const formData = $('#round-form').serialize();
        console.log("Submit");
        console.log(formData);
        socket.emit('run_round', { data: formData });
    });

    $(document).on("input", "#slider", function () {
        $("#demand").val($(this).val());
    });
    
    $(document).on("input", "#demand", function () {
        let value = parseInt($(this).val(), 10);
        let min = parseInt($(this).attr("min"), 10);
        let max = parseInt($(this).attr("max"), 10);

        if (value < min) $(this).val(min);
        if (value > max) $(this).val(max);
        
        $("#slider").val($(this).val()); // Keep slider in sync
    });

    function updateLeader(profits_gains, phase){
        if(phase ==0){
            $('#playerGains').html(profits_gains["gains"]);
        }
        else if(phase ==1){
            $('#playerGains').html(profits_gains["gains_AE"]);
        }
        else{
            console.log("improper phase");
        }
    }

    function updateGraph(data, phase) {
        console.log("updateGraph phase=", phase);
        console.log("graphData used:", phase === 0 ? data.graphData : data.graphDataAE);


        let config = {
            displayModeBar: false, // This removes the toolbar
            displaylogo: false, // This removes the Plotly logo
            scrollZoom: false, // Disable zoom on scroll
            staticPlot: false, // Allow hover interactions without panning or zooming
            editable: false  // Disable editing
        };
        
        ////////////////////////
        ///GRAPH BEFORE EVENT///
        ////////////////////////
        if(phase ==0) {

            // No image
            $('#myImage').css('visibility', 'hidden');
                               
            console.log("Phase 0, show graph");

            const in_data = data["graphData"]
            const graph = document.querySelector('.bidGraph');
            const demand = in_data["demand"]
            const marketPrice = in_data["marketPrice"]
            const xList = in_data["xList"] // Center of Bar (to[0] - from[0] / 2)
            const widthBar = in_data["widthBar"] // Width from left to right
            const barHeight = in_data["barHeight"] // Height of bar
            const colors = in_data["colors"]
            const players = in_data["players"]
            const costs = in_data["costs"] // Total cost for each player
            const assets = in_data["assets"]
            const roundNumber = data["roundNumber"]

            if (!sharedXRange) {
                const totalWidth = widthBar.reduce((acc, w) => acc + w, 0);
                const maxX = Math.max(totalWidth, demand);
                const roundedMaxX = Math.ceil(maxX / 10) * 10;
                sharedXRange = [0, roundedMaxX+200];
            }

            let data_before = [
                {
                    type: 'bar',
                    x: xList,
                    y: barHeight,
                    width: widthBar,
                    name: "Bids Before the Event",
                    marker: {
                        color: colors
                    },
                    hovertext: widthBar.map((w, i) => `<b>${players[i]}</b><br>Asset: ${assets[i]}<br>Quantity: ${w}<br>Price: ${barHeight[i]}`), 
                    hoverinfo: "text"
                },
            ];

            let shapes_list_before = [
                // Horizontal line (Market Price)
                {
                    type: "line",
                    x0: 0,  // Start at the min X value
                    x1: Math.max(widthBar.reduce((acc, cur) => acc + cur, 0), demand),  // End at the max X value
                    y0: marketPrice,
                    y1: marketPrice,
                    line: {
                        color: "red",
                        width: 3,
                        dash: "dash"
                    }
                },
                // Vertical line (Demand)
                {
                    type: "line",
                    x0: demand,
                    x1: demand,
                    y0: 0,  // Start at the minimum y value (log(1) = 0)
                    y1: 100000,  // Extend beyond max y value in log scale
                    line: {
                        color: "black",
                        width: 3,
                        dash: "dash"
                    }
                }
            ]

            let currentX = 0;

            for (let i = 0; i < xList.length; i++) {
                const width = widthBar[i];
                const cost = costs[i];

                const barCenter = currentX + width / 2;
                const halfWidth = width / 2;

                shapes_list_before.push({
                    type: 'line',
                    x0: barCenter - halfWidth,
                    x1: barCenter + halfWidth,
                    y0: cost,
                    y1: cost,
                    line: {
                        color: 'blue',
                        width: 2,
                        dash: 'solid'
                    }
                });

                currentX += width;
            }

            var layout = {
                barmode: 'overlay',
                title: {
                    text: `Electricity Market Round ${roundNumber - 1} (without event)`
                },
                xaxis: {
                    title: {
                        text: 'Quantity (MW)'  // 🡐 Your custom x-axis label
                    },
                    range: sharedXRange
                },
                yaxis: {
                    title: {
                        text: 'Price ($/MWh)'  // 🡐 Your custom y-axis label
                    },
                    type: 'log',
                    range: [0,5],
                    tickmode: 'array',
                    tickvals: [1, 10, 100, 1000, 10000], // The values at which to show ticks
                    ticktext: ['1', '10', '100', '1000', '10000'], // Custom labels for the ticks
                },
                dragmode: false,
                shapes: shapes_list_before,
                annotations: [
                    {
                        x: Math.max(widthBar.reduce((acc, cur) => acc + cur, 0), demand),  
                        y: Math.log10(marketPrice),
                        xanchor: "left",
                        yanchor: "middle",
                        text: `Market Price: ${marketPrice}`,
                        showarrow: true,
                        arrowcolor: "red",
                        ax: 20,  // Move the arrowhead to the right
                        ay: 0,  // Keep the arrow aligned horizontally
                        font: {
                            color: "red",
                            size: 14
                        } // This is important
                    },
                    // Demand Label
                    {
                        x: demand,
                        y: Math.log10(10000),  
                        xanchor: "right",
                        yanchor: "bottom",
                        text: `Demand: ${demand}`,
                        showarrow: true,
                        arrowcolor: "black",
                        ax: -20,  // Move the arrowhead to the right
                        ay: -10,  // Keep the arrow aligned horizontally
                        font: {
                            color: "black",
                            size: 14
                        }
                    }
                ]
            };

            Plotly.newPlot(graph, data_before, layout, config);
        }

        ///////////////////////
        ///GRAPH AFTER EVENT///
        ///////////////////////
        else if(phase == 1){

            

            console.log("Phase 1, show graph");
            // After Event (AE)
            const graph = document.querySelector('.bidGraph');
            const in_data_AE = data["graphDataAE"]
            const xList_AE = in_data_AE["xList"] // Center of Bar (to[0] - from[0] / 2)
            const costs_AE = in_data_AE["costs"] // Total cost for each player
            const widthBar_AE = in_data_AE["widthBar"] // Width from left to right
            const barHeight_AE = in_data_AE["barHeight"] // Height of bar
            const players_AE = in_data_AE["players"]
            const colors_AE = in_data_AE["colors"]
            const marketPrice_AE = in_data_AE["marketPrice"]
            const demand_AE = in_data_AE["demand"]
            const assets_AE = in_data_AE["assets"]
            const roundNumber = data["roundNumber"]
            const event = data["event"]
            const event_name = event["event_name"]
            const event_tag = event["event_tag"]

            // Change Image
            if(event_tag == "none"){
                $('#myImage').css('visibility', 'hidden');
            }
            else{
                let imagePath = "/static/images/"+encodeURIComponent(event_tag)+".png";
                $('#myImage').attr('src', imagePath).css('visibility', 'visible');;
            }
            

            

            let data_AE = [
                {
                    type: 'bar',
                    x: xList_AE,
                    y: barHeight_AE,
                    width: widthBar_AE,
                    name: "Bids After the Event",
                    marker: { color: colors_AE },
                    hovertext: widthBar_AE.map((w, i) => `<b>${players_AE[i]}</b><br>Asset: ${assets_AE[i]}<br>Quantity: ${w}<br>Price: ${barHeight_AE[i]}`),
                    hoverinfo: "text"
                },
            ];

            
            let shapes_list_AE = [
                    // Horizontal line (Market Price)
                    {
                        type: "line",
                        x0: 0,  // Start at the min X value
                        x1: Math.max(widthBar_AE.reduce((acc, cur) => acc + cur, 0), demand_AE),  // End at the max X value
                        y0: marketPrice_AE,
                        y1: marketPrice_AE,
                        line: {
                            color: "red",
                            width: 3,
                            dash: "dash"
                        }
                    },
                    // Vertical line (Demand)
                    {
                        type: "line",
                        x0: demand_AE,
                        x1: demand_AE,
                        y0: 0,  // Start at the minimum y value (log(1) = 0)
                        y1: 100000,  // Extend beyond max y value in log scale
                        line: {
                            color: "black",
                            width: 3,
                            dash: "dash"
                        }
                    }
                ]

            let currentX = 0;

            for (let i = 0; i < xList_AE.length; i++) {
                const width = widthBar_AE[i];
                const cost = costs_AE[i];

                const barCenter = currentX + width / 2;
                const halfWidth = width / 2;

                shapes_list_AE.push({
                    type: 'line',
                    x0: barCenter - halfWidth,
                    x1: barCenter + halfWidth,
                    y0: cost,
                    y1: cost,
                    line: {
                        color: 'blue',
                        width: 2,
                        dash: 'solid'
                    }
                });

                currentX += width;
            }

            var layout = {
                barmode: 'overlay',
                title: {
                    text: `Electricity Market Round ${roundNumber - 1} (${event_name})`
                },
                xaxis: {
                    title: {
                        text: 'Quantity (MW)'  // 🡐 Your custom x-axis label
                    },
                    range: sharedXRange
                },
                yaxis: {
                    title: {
                        text: 'Price ($/MWh)'  // 🡐 Your custom y-axis label
                    },
                    type: 'log',
                    range: [0,5],
                    tickmode: 'array',
                    tickvals: [1, 10, 100, 1000, 10000], // The values at which to show ticks
                    ticktext: ['1', '10', '100', '1000', '10000'], // Custom labels for the ticks
                },
                dragmode: false,
                shapes: shapes_list_AE,
                annotations: [
                    {
                        x: Math.max(widthBar_AE.reduce((acc, cur) => acc + cur, 0), demand_AE),  
                        y: Math.log10(marketPrice_AE),
                        xanchor: "left",
                        yanchor: "middle",
                        text: `Market Price: ${marketPrice_AE}`,
                        showarrow: true,
                        arrowcolor: "red",
                        ax: 20,  // Move the arrowhead to the right
                        ay: 0,  // Keep the arrow aligned horizontally
                        font: {
                            color: "red",
                            size: 14
                        } // This is important
                    },
                    // Demand Label
                    {
                        x: demand_AE,
                        y: Math.log10(10000),  
                        xanchor: "right",
                        yanchor: "bottom",
                        text: `Demand: ${demand_AE}`,
                        showarrow: true,
                        arrowcolor: "black",
                        ax: -20,  // Move the arrowhead to the right
                        ay: -10,  // Keep the arrow aligned horizontally
                        font: {
                            color: "black",
                            size: 14
                        }
                    }
                ]
            };

            Plotly.newPlot(graph, data_AE, layout, config);
        }
        

  
        else{
            console.log("IMPROPER PHASE");
        }
        
    }
});
