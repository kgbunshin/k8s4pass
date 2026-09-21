---
id: rollout-001
domain: application-deployment
topic: rolling-updates
difficulty: 2
points: 5
namespaces: ["{{ns}}"]
check_timeout: 90
vars:
  ns: [rel, deploy, prod]
  replicas: [3, 4]
---

## Statement

In namespace `{{ns}}` the Deployment `web` was just updated to `nginx:1.27` and the release turned out to be bad.

1. Roll it back to the previous revision (`nginx:1.26`).
2. Configure its rolling update strategy with `maxSurge: 1` and `maxUnavailable: 0`.
3. Make sure all {{replicas}} replicas end up `Ready`.

## Setup

```bash
kubectl create namespace {{ns}}
kubectl -n {{ns}} create deployment web --image=nginx:1.26 --replicas={{replicas}}
kubectl -n {{ns}} rollout status deployment/web --timeout=90s
kubectl -n {{ns}} set image deployment/web nginx=nginx:1.27
kubectl -n {{ns}} rollout status deployment/web --timeout=90s
```

## Check

```
2|Deployment web runs nginx:1.26 again|[ "$(kubectl -n {{ns}} get deploy web -o jsonpath='{.spec.template.spec.containers[0].image}')" = "nginx:1.26" ]
2|strategy has maxSurge 1 and maxUnavailable 0|[ "$(kubectl -n {{ns}} get deploy web -o jsonpath='{.spec.strategy.rollingUpdate.maxSurge} {.spec.strategy.rollingUpdate.maxUnavailable}')" = "1 0" ]
1|all {{replicas}} replicas are Ready on nginx:1.26|[ "$(kubectl -n {{ns}} get deploy web -o jsonpath='{.spec.template.spec.containers[0].image}')" = "nginx:1.26" ] && kubectl -n {{ns}} rollout status deploy/web --timeout=90s >/dev/null && [ "$(kubectl -n {{ns}} get deploy web -o jsonpath='{.status.readyReplicas}')" = "{{replicas}}" ]
```

## Solution: rollout undo

```bash
kubectl -n {{ns}} rollout undo deployment/web
kubectl -n {{ns}} patch deployment web -p '{"spec":{"strategy":{"type":"RollingUpdate","rollingUpdate":{"maxSurge":1,"maxUnavailable":0}}}}'
kubectl -n {{ns}} rollout status deployment/web --timeout=90s
```

## Solution: set image and merge patch

```bash
kubectl -n {{ns}} set image deployment/web nginx=nginx:1.26
kubectl -n {{ns}} patch deployment web --type=merge -p '{"spec": {"strategy": {"rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}}}'
kubectl -n {{ns}} rollout status deployment/web --timeout=90s
```
