---
id: ingress-001
domain: services-networking
topic: ingress
difficulty: 2
points: 6
namespaces: ["{{ns}}"]
check_timeout: 60
vars:
  ns: [web, shop, site]
  host: [shop.example.com, app.example.org]
  path: [/shop, /store]
---

## Statement

In namespace `{{ns}}` the Service `web` (port 80) already exists.

Create an Ingress named `web-ingress` that uses the IngressClass `nginx` and routes the host `{{host}}`, path `{{path}}` (path type `Prefix`), to the Service `web` on port 80.

## Setup

```bash
kubectl create namespace {{ns}}
kubectl -n {{ns}} create deployment web --image=nginx:1.27
kubectl -n {{ns}} expose deployment web --port=80
```

## Check

```
1|Ingress web-ingress exists|kubectl -n {{ns}} get ingress web-ingress
1|ingressClassName is nginx|[ "$(kubectl -n {{ns}} get ingress web-ingress -o jsonpath='{.spec.ingressClassName}')" = "nginx" ]
1|host is {{host}}|[ "$(kubectl -n {{ns}} get ingress web-ingress -o jsonpath='{.spec.rules[0].host}')" = "{{host}}" ]
1|path {{path}} with pathType Prefix|[ "$(kubectl -n {{ns}} get ingress web-ingress -o jsonpath='{.spec.rules[0].http.paths[0].path} {.spec.rules[0].http.paths[0].pathType}')" = "{{path}} Prefix" ]
2|backend is Service web on port 80|[ "$(kubectl -n {{ns}} get ingress web-ingress -o jsonpath='{.spec.rules[0].http.paths[0].backend.service.name} {.spec.rules[0].http.paths[0].backend.service.port.number}')" = "web 80" ]
```

## Solution: imperative

```bash
kubectl -n {{ns}} create ingress web-ingress --class=nginx --rule="{{host}}{{path}}*=web:80"
```

## Solution: manifest

```bash
kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: web-ingress
  namespace: {{ns}}
spec:
  ingressClassName: nginx
  rules:
    - host: {{host}}
      http:
        paths:
          - path: {{path}}
            pathType: Prefix
            backend:
              service:
                name: web
                port:
                  number: 80
EOF
```
