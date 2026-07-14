import { io } from "https://cdn.socket.io/4.8.1/socket.io.esm.min.js";

$(document).ready(function() {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const socket = io("/lobby", {
        auth: { csrf_token: csrfToken }
    });

    socket.on('player_left', (data) => {
        console.log("Last player")
        document.querySelector('#logout-form')?.requestSubmit();
    });

    socket.on('user_change', (data) => {
        console.log(data);
        $("#player-total").text(data["players"].length);

        const playerList = $('#player-list').empty();
        $.each(data["players"], function (index, player) {
            $('<li>').text(player["username"]).appendTo(playerList);
        });
    });

    $('#start').click(function(e) {
        e.preventDefault();
        socket.emit('start_game', { message: 'Start the game' });
    });

    socket.on('game_start', (data) => {
        location.href = '/game'; 
    });
});
