---
id: logs-001
domain: application-observability-maintenance
topic: logging
difficulty: 1
points: 4
namespaces: ["{{ns}}"]
check_timeout: 30
vars:
  ns: [logs, trace, watch]
  token: [a1b2c3, x9y8z7, k4m5n6]
---

## Statement

The Pod `emitter` in namespace `{{ns}}` writes several lines to its log. One of them has the form `TOKEN=<value>`.

Find the value and store it (only the value, not `TOKEN=`) in a ConfigMap named `answer`, under the key `token`.

## Setup

```bash
kubectl create namespace {{ns}}
kubectl -n {{ns}} run emitter --image=busybox:1.36 -- sh -c 'echo starting; echo TOKEN={{token}}; echo finished; sleep 3600'
kubectl -n {{ns}} wait --for=condition=Ready pod/emitter --timeout=90s
sleep 2
```

## Check

```
1|ConfigMap answer exists|kubectl -n {{ns}} get cm answer
3|key token holds the value found in the pod log|[ "$(kubectl -n {{ns}} get cm answer -o jsonpath='{.data.token}')" = "{{token}}" ]
```

## Solution: grep and cut

```bash
token=$(kubectl -n {{ns}} logs emitter | grep '^TOKEN=' | cut -d= -f2)
kubectl -n {{ns}} create configmap answer --from-literal=token="$token"
```

## Solution: sed and apply

```bash
token=$(kubectl -n {{ns}} logs pod/emitter | sed -n 's/^TOKEN=//p')
kubectl -n {{ns}} create configmap answer --from-literal=token="$token" --dry-run=client -o yaml | kubectl apply -f -
```
