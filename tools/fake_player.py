"""Diagnostic remote Player 2 for the one-PC KerMP vertical slice."""
from __future__ import annotations

import argparse
import json
import socket
import uuid


def send(sock, typ, player_id, payload=None):
    sock.sendall((json.dumps({'type': typ, 'payload': payload or {}, 'sender_id': player_id,
                              'message_id': str(uuid.uuid4()), 'sent_at': 0, 'protocol_version': 1}) + '\n').encode())


def main():
    ap = argparse.ArgumentParser(prog='kermp-fake-player')
    ap.add_argument('host')
    ap.add_argument('--port', type=int, default=17653)
    ap.add_argument('--name', default='Player2')
    ap.add_argument('--player-id', default='player2')
    args = ap.parse_args()
    with socket.create_connection((args.host, args.port)) as sock:
        send(sock, 'hello', args.player_id, {'display_name': args.name})
        print(sock.makefile('rb').readline().decode().strip())
        print('Commands: select <sim_id>; interact <affordance_id> [target_id]; snapshot; quit')
        while True:
            parts = input('player2> ').split()
            if not parts: continue
            if parts[0] == 'quit': return
            if parts[0] == 'select' and len(parts) == 2:
                send(sock, 'sim.select', args.player_id, {'sim_id': parts[1]})
            elif parts[0] == 'interact' and len(parts) in (2, 3):
                send(sock, 'interaction.request', args.player_id, {
                    'request_id': str(uuid.uuid4()), 'sim_id': None, 'affordance_id': parts[1],
                    'target_id': parts[2] if len(parts) == 3 else None})
            elif parts[0] == 'snapshot':
                send(sock, 'snapshot.request', args.player_id)
            else:
                print('bad command')


if __name__ == '__main__':
    main()
