---
id: quota-001
domain: application-environment-config-security
topic: resource-quotas
difficulty: 2
points: 6
namespaces: ["{{ns}}"]
check_timeout: 30
vars:
  ns: [limited, capped, sized]
  pods: [5, 8]
  cpu: [2, 4]
  mem: [2Gi, 4Gi]
---

## Statement

In namespace `{{ns}}`:

1. Create a ResourceQuota named `compute-quota` that allows at most {{pods}} pods, `requests.cpu` of `{{cpu}}` and `requests.memory` of `{{mem}}`.
2. Create a LimitRange named `defaults` so that containers without explicit resources get the default **limits** `cpu: 200m`, `memory: 128Mi` and the default **requests** `cpu: 100m`, `memory: 64Mi`.

## Setup

```bash
kubectl create namespace {{ns}}
```

## Check

```
1|quota compute-quota limits pods to {{pods}}|[ "$(kubectl -n {{ns}} get resourcequota compute-quota -o jsonpath='{.spec.hard.pods}')" = "{{pods}}" ]
1|quota limits requests.cpu to {{cpu}}|[ "$(kubectl -n {{ns}} get resourcequota compute-quota -o jsonpath='{.spec.hard.requests\.cpu}')" = "{{cpu}}" ]
1|quota limits requests.memory to {{mem}}|[ "$(kubectl -n {{ns}} get resourcequota compute-quota -o jsonpath='{.spec.hard.requests\.memory}')" = "{{mem}}" ]
1|LimitRange defaults: limits cpu 200m, memory 128Mi|[ "$(kubectl -n {{ns}} get limitrange defaults -o jsonpath='{.spec.limits[0].default.cpu} {.spec.limits[0].default.memory}')" = "200m 128Mi" ]
1|LimitRange defaults: requests cpu 100m, memory 64Mi|[ "$(kubectl -n {{ns}} get limitrange defaults -o jsonpath='{.spec.limits[0].defaultRequest.cpu} {.spec.limits[0].defaultRequest.memory}')" = "100m 64Mi" ]
1|a pod without resources gets the default limits (server-side dry run)|[ "$(kubectl -n {{ns}} run probe --image=busybox:1.36 --dry-run=server -o jsonpath='{.spec.containers[0].resources.limits.cpu}')" = "200m" ]
```

## Solution: create quota and apply the LimitRange

```bash
kubectl -n {{ns}} create quota compute-quota --hard=pods={{pods}},requests.cpu={{cpu}},requests.memory={{mem}}
kubectl apply -f - <<EOF
apiVersion: v1
kind: LimitRange
metadata:
  name: defaults
  namespace: {{ns}}
spec:
  limits:
    - type: Container
      default:
        cpu: 200m
        memory: 128Mi
      defaultRequest:
        cpu: 100m
        memory: 64Mi
EOF
```

## Solution: manifests

```bash
kubectl -n {{ns}} apply -f - <<EOF
apiVersion: v1
kind: ResourceQuota
metadata:
  name: compute-quota
spec:
  hard:
    pods: "{{pods}}"
    requests.cpu: "{{cpu}}"
    requests.memory: {{mem}}
---
apiVersion: v1
kind: LimitRange
metadata:
  name: defaults
spec:
  limits:
    - type: Container
      defaultRequest: {cpu: 100m, memory: 64Mi}
      default: {cpu: 200m, memory: 128Mi}
EOF
```
