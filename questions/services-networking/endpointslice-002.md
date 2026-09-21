---
id: endpointslice-002
domain: services-networking
topic: endpointslices
difficulty: 2
points: 6
namespaces: ["{{ns}}"]
check_timeout: 90
vars:
  ns: [shop, api, front]
  replicas: [2, 3]
  target: [5, 6]
---

## Statement

In namespace `{{ns}}` the Deployment `web` (image `nginx:1.27`, {{replicas}} replicas) is exposed by the Service `web`.

1. Find the EndpointSlice that belongs to the Service `web` and store its name in a ConfigMap named `slice-info`, under the key `slice`.
2. Scale the Deployment `web` to {{target}} replicas and make sure the EndpointSlice lists {{target}} ready endpoints.

## Setup

```bash
kubectl create namespace {{ns}}
kubectl -n {{ns}} create deployment web --image=nginx:1.27 --replicas={{replicas}}
kubectl -n {{ns}} expose deployment web --port=80
kubectl -n {{ns}} rollout status deployment/web --timeout=90s
```

## Check

```
2|ConfigMap slice-info holds the real EndpointSlice name|[ "$(kubectl -n {{ns}} get cm slice-info -o jsonpath='{.data.slice}')" = "$(kubectl -n {{ns}} get endpointslices.discovery.k8s.io -l kubernetes.io/service-name=web -o jsonpath='{.items[0].metadata.name}')" ]
2|Deployment web has {{target}} replicas|[ "$(kubectl -n {{ns}} get deploy web -o jsonpath='{.spec.replicas}')" = "{{target}}" ]
2|the EndpointSlice lists {{target}} ready endpoints|[ "$(kubectl -n {{ns}} get endpointslices.discovery.k8s.io -l kubernetes.io/service-name=web -o jsonpath='{range .items[*].endpoints[*]}{.conditions.ready}{"\n"}{end}' | grep -c true)" = "{{target}}" ]
```

## Solution: kubectl

```bash
slice=$(kubectl -n {{ns}} get endpointslices.discovery.k8s.io -l kubernetes.io/service-name=web -o jsonpath='{.items[0].metadata.name}')
kubectl -n {{ns}} create configmap slice-info --from-literal=slice="$slice"
kubectl -n {{ns}} scale deployment web --replicas={{target}}
kubectl -n {{ns}} rollout status deployment/web --timeout=90s
```

## Solution: patch and apply

```bash
slice=$(kubectl -n {{ns}} get endpointslices.discovery.k8s.io -l kubernetes.io/service-name=web -o name | cut -d/ -f2)
kubectl -n {{ns}} create configmap slice-info --from-literal=slice="$slice" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n {{ns}} patch deployment web --type=merge -p '{"spec": {"replicas": {{target}} }}'
kubectl -n {{ns}} rollout status deployment/web --timeout=90s
```
