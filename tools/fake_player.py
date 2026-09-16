"""Diagnostic remote Player 2 for the one-PC KerMP vertical slice."""
from __future__ import annotations

import argparse
import json
import socket
import uuid
import threading

from kermp.compatibility import CompatibilityManifest
from kermp.protocol import PROTOCOL_VERSION


def send(sock, typ, player_id, payload=None):
    sock.sendall((json.dumps({'type': typ, 'payload': payload or {}, 'sender_id': player_id,
                              'message_id': str(uuid.uuid4()), 'sent_at': 0, 'protocol_version': PROTOCOL_VERSION}) + '\n').encode())


def main():
    ap = argparse.ArgumentParser(prog='kermp-fake-player')
    ap.add_argument('host')
    ap.add_argument('--port', type=int, default=17653)
    ap.add_argument('--name', default='Player2')
    ap.add_argument('--player-id', default='player2')
    ap.add_argument('--auto-travel-ack', action='store_true')
    args = ap.parse_args()
    with socket.create_connection((args.host, args.port)) as sock:
        manifest = CompatibilityManifest.local(args.player_id, args.name)
        send(sock, 'hello', args.player_id, {'display_name': args.name, 'manifest': manifest.to_dict()})
        sock_file = sock.makefile('rb')
        print(sock_file.readline().decode().strip())
        stop = threading.Event()
        def reader():
            while not stop.is_set():
                line = sock_file.readline()
                if not line:
                    return
                try:
                    msg = json.loads(line.decode())
                    typ, p = msg.get('type'), msg.get('payload') or {}
                    if typ == 'travel.propose':
                        print('travel.propose epoch=%s txn=%s zone=%s' % (p.get('epoch'), p.get('txn_id'), p.get('zone_id')), flush=True)
                        if args.auto_travel_ack:
                            send(sock, 'travel.ready', args.player_id, {'txn_id': p.get('txn_id'), 'epoch': p.get('epoch')})
                    elif typ == 'travel.commit':
                        print('travel.commit epoch=%s txn=%s zone=%s (diagnostic fake participant)' % (p.get('epoch'), p.get('txn_id'), p.get('zone_id')), flush=True)
                        if args.auto_travel_ack:
                            send(sock, 'travel.zone_ready', args.player_id, {'txn_id': p.get('txn_id'), 'epoch': p.get('epoch'), 'zone_id': p.get('zone_id')})
                    elif typ in ('travel.resume', 'travel.abort'):
                        print('%s epoch=%s txn=%s' % (typ, p.get('epoch'), p.get('txn_id')), flush=True)
                except Exception as exc:
                    print('receiver error: %s' % exc, flush=True)
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        print('Commands: select <sim_id>; interact <affordance_id> [target_id]; snapshot; quit')
        while True:
            parts = input('player2> ').split()
            if not parts: continue
            if parts[0] == 'quit': stop.set(); return
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
