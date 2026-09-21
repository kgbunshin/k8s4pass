#!/usr/bin/env bash
# Brings up/tears down the kind cluster used to validate questions.
# Uses its own kubeconfig (ckad-lab/.kubeconfig): it does NOT touch ~/.kube/config
# or your current kubectl context.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME=ckad-val
KUBECONFIG_FILE="$ROOT/.kubeconfig"

case "${1:-up}" in
  up)
    if kind get clusters 2>/dev/null | grep -qx "$NAME"; then
      kind export kubeconfig --name "$NAME" --kubeconfig "$KUBECONFIG_FILE"
      echo "cluster '$NAME' already exists"
    else
      kind create cluster --name "$NAME" --kubeconfig "$KUBECONFIG_FILE" --wait 180s
    fi
    chmod 600 "$KUBECONFIG_FILE"
    echo "context: kind-$NAME (kubeconfig: $KUBECONFIG_FILE)"
    ;;
  down)
    kind delete cluster --name "$NAME" --kubeconfig "$KUBECONFIG_FILE"
    rm -f "$KUBECONFIG_FILE"
    ;;
  *)
    echo "usage: $0 [up|down]" >&2
    exit 2
    ;;
esac
