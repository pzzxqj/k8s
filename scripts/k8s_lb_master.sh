#!/bin/bash
# Keepalived VRRP transition handler: start/stop HAProxy with the VIP ownership.
# Called by keepalived notify_master/notify_backup/notify_fault with start|stop,
# so HAProxy only ever binds the VIP on the node currently holding it (avoiding
# an EADDRINUSE against the local kube-apiserver which binds the node's own IP).
set -euo pipefail

case "${1:-}" in
    start)
        systemctl start haproxy
        ;;
    stop)
        systemctl stop haproxy
        ;;
    *)
        echo "usage: $0 start|stop" >&2
        exit 1
        ;;
esac
