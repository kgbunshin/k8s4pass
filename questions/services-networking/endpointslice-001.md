---
id: endpointslice-001
domain: services-networking
topic: endpointslices
difficulty: 2
points: 8
namespaces: ["{{ns}}"]
check_timeout: 30
vars:
  ns: [net, edge, ext]
  svc: [db, cache, legacy]
  ip: [10.20.30.40, 192.168.50.10, 172.31.5.9]
  port: [5432, 6379, 8080]
---

## Statement

In namespace `{{ns}}` the Service `{{svc}}` exists without a selector: it fronts a server that runs outside the cluster.

Create an EndpointSlice named `{{svc}}-external` in the same namespace that:

- belongs to the Service `{{svc}}`;
- has address type `IPv4`;
- contains one endpoint with address `{{ip}}`, marked as ready;
- exposes port `{{port}}` over TCP, named `tcp` (the name of the Service port).

## Setup

```bash
kubectl create namespace {{ns}}
kubectl apply -f - <<EOF
apiVersion: v1
kind: Service
metadata:
  name: {{svc}}
  namespace: {{ns}}
spec:
  ports:
    - name: tcp
      port: 80
      targetPort: {{port}}
EOF
```

## Check

```
1|EndpointSlice {{svc}}-external exists|kubectl -n {{ns}} get endpointslices.discovery.k8s.io {{svc}}-external
2|it belongs to Service {{svc}} (kubernetes.io/service-name label)|[ "$(kubectl -n {{ns}} get endpointslices.discovery.k8s.io {{svc}}-external -o jsonpath='{.metadata.labels.kubernetes\.io/service-name}')" = "{{svc}}" ]
1|address type is IPv4|[ "$(kubectl -n {{ns}} get endpointslices.discovery.k8s.io {{svc}}-external -o jsonpath='{.addressType}')" = "IPv4" ]
2|endpoint {{ip}} is ready|[ "$(kubectl -n {{ns}} get endpointslices.discovery.k8s.io {{svc}}-external -o jsonpath='{.endpoints[0].addresses[0]} {.endpoints[0].conditions.ready}')" = "{{ip}} true" ]
2|port {{port}}/TCP named tcp|[ "$(kubectl -n {{ns}} get endpointslices.discovery.k8s.io {{svc}}-external -o jsonpath='{.ports[0].port} {.ports[0].protocol} {.ports[0].name}')" = "{{port}} TCP tcp" ]
```

## Solution: yaml

```bash
kubectl apply -f - <<EOF
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: {{svc}}-external
  namespace: {{ns}}
  labels:
    kubernetes.io/service-name: {{svc}}
addressType: IPv4
endpoints:
  - addresses:
      - "{{ip}}"
    conditions:
      ready: true
ports:
  - name: tcp
    port: {{port}}
    protocol: TCP
EOF
```

## Solution: json

```bash
kubectl create -f - <<EOF
{
  "apiVersion": "discovery.k8s.io/v1",
  "kind": "EndpointSlice",
  "metadata": {
    "name": "{{svc}}-external",
    "namespace": "{{ns}}",
    "labels": { "kubernetes.io/service-name": "{{svc}}" }
  },
  "addressType": "IPv4",
  "endpoints": [ { "addresses": ["{{ip}}"], "conditions": { "ready": true } } ],
  "ports": [ { "name": "tcp", "port": {{port}} } ]
}
EOF
```
