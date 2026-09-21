---
id: service-001
domain: services-networking
topic: services
difficulty: 1
points: 5
namespaces: ["{{ns}}"]
check_timeout: 90
vars:
  ns: [svc, expose, ports]
  np: [30080, 30090, 31000]
---

## Statement

In namespace `{{ns}}` the Deployment `web` (2 replicas) exists.

Expose it with a Service named `web-np` of type `NodePort`: service port `8080`, target port `80` and node port `{{np}}`.

## Setup

```bash
kubectl create namespace {{ns}}
kubectl -n {{ns}} create deployment web --image=nginx:1.27 --replicas=2
kubectl -n {{ns}} rollout status deployment/web --timeout=90s
```

## Check

```
1|Service web-np is of type NodePort|[ "$(kubectl -n {{ns}} get svc web-np -o jsonpath='{.spec.type}')" = "NodePort" ]
2|port 8080, targetPort 80, nodePort {{np}}|[ "$(kubectl -n {{ns}} get svc web-np -o jsonpath='{.spec.ports[0].port} {.spec.ports[0].targetPort} {.spec.ports[0].nodePort}')" = "8080 80 {{np}}" ]
2|the Service routes to the 2 web pods (2 ready endpoints)|[ "$(kubectl -n {{ns}} get endpointslices.discovery.k8s.io -l kubernetes.io/service-name=web-np -o jsonpath='{range .items[*].endpoints[*]}{.conditions.ready}{"\n"}{end}' | grep -c true)" = "2" ]
```

## Solution: expose and patch

```bash
kubectl -n {{ns}} expose deployment web --name=web-np --type=NodePort --port=8080 --target-port=80
kubectl -n {{ns}} patch service web-np --type=json -p '[{"op":"replace","path":"/spec/ports/0/nodePort","value":{{np}}}]'
```

## Solution: manifest

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Service
metadata:
  name: web-np
  namespace: {{ns}}
spec:
  type: NodePort
  selector:
    app: web
  ports:
    - port: 8080
      targetPort: 80
      nodePort: {{np}}
EOF
```
