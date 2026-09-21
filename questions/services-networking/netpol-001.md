---
id: netpol-001
domain: services-networking
topic: network-policy
difficulty: 2
points: 6
namespaces: ["{{ns}}"]
check_timeout: 30
vars:
  ns: [secured, shield, mesh]
  client: [frontend, api, web]
  port: [5432, 3306, 6379]
---

## Statement

In namespace `{{ns}}`, create a NetworkPolicy named `db-allow` that:

- applies to the pods labeled `app=db`;
- allows ingress **only** from pods labeled `app={{client}}` (in the same namespace) and only on TCP port `{{port}}`;
- affects ingress traffic only (do not restrict egress).

## Setup

```bash
kubectl create namespace {{ns}}
```

## Check

```
1|NetworkPolicy db-allow exists|kubectl -n {{ns}} get networkpolicy db-allow
1|it selects the pods app=db|[ "$(kubectl -n {{ns}} get networkpolicy db-allow -o jsonpath='{.spec.podSelector.matchLabels.app}')" = "db" ]
1|policyTypes is only Ingress|[ "$(kubectl -n {{ns}} get networkpolicy db-allow -o jsonpath='{.spec.policyTypes[*]}')" = "Ingress" ]
2|ingress is allowed from pods app={{client}}|[ "$(kubectl -n {{ns}} get networkpolicy db-allow -o jsonpath='{.spec.ingress[0].from[0].podSelector.matchLabels.app}')" = "{{client}}" ]
1|only TCP port {{port}} is allowed|[ "$(kubectl -n {{ns}} get networkpolicy db-allow -o jsonpath='{.spec.ingress[0].ports[0].port} {.spec.ingress[0].ports[0].protocol}')" = "{{port}} TCP" ]
```

## Solution: explicit policyTypes

```bash
kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: db-allow
  namespace: {{ns}}
spec:
  podSelector:
    matchLabels:
      app: db
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: {{client}}
      ports:
        - protocol: TCP
          port: {{port}}
EOF
```

## Solution: defaults left implicit

```bash
kubectl -n {{ns}} create -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: db-allow
spec:
  podSelector: {matchLabels: {app: db}}
  ingress:
    - from:
        - podSelector: {matchLabels: {app: {{client}}}}
      ports:
        - port: {{port}}
EOF
```
