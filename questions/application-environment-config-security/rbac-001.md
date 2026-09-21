---
id: rbac-001
domain: application-environment-config-security
topic: rbac
difficulty: 2
points: 8
namespaces: ["{{ns}}"]
check_timeout: 30
vars:
  ns: [dev, team, ci]
  sa: [deployer, robot, auditor]
---

## Statement

In namespace `{{ns}}`:

1. Create a ServiceAccount named `{{sa}}`.
2. Create a Role named `viewer` that allows `get`, `list` and `watch` on `pods` and on `deployments` (API group `apps`).
3. Create a RoleBinding named `viewer-binding` that grants the Role to the ServiceAccount.

The ServiceAccount must not get any other permission, and it must not be able to read Pods in other namespaces.

## Setup

```bash
kubectl create namespace {{ns}}
```

## Check

```
1|ServiceAccount {{sa}} exists|kubectl -n {{ns}} get sa {{sa}}
1|Role viewer exists (namespaced)|kubectl -n {{ns}} get role viewer
1|RoleBinding viewer-binding binds Role viewer to {{sa}}|[ "$(kubectl -n {{ns}} get rolebinding viewer-binding -o jsonpath='{.subjects[0].name} {.roleRef.name}')" = "{{sa}} viewer" ]
2|{{sa}} can list pods and get deployments in {{ns}}|[ "$(kubectl auth can-i list pods -n {{ns}} --as=system:serviceaccount:{{ns}}:{{sa}})" = "yes" ] && [ "$(kubectl auth can-i get deployments.apps -n {{ns}} --as=system:serviceaccount:{{ns}}:{{sa}})" = "yes" ]
2|{{sa}} cannot delete pods or read secrets (least privilege)|[ "$(kubectl auth can-i list pods -n {{ns}} --as=system:serviceaccount:{{ns}}:{{sa}})" = "yes" ] && [ "$(kubectl auth can-i delete pods -n {{ns}} --as=system:serviceaccount:{{ns}}:{{sa}})" = "no" ] && [ "$(kubectl auth can-i get secrets -n {{ns}} --as=system:serviceaccount:{{ns}}:{{sa}})" = "no" ]
1|{{sa}} cannot list pods in other namespaces|[ "$(kubectl auth can-i list pods -n {{ns}} --as=system:serviceaccount:{{ns}}:{{sa}})" = "yes" ] && [ "$(kubectl auth can-i list pods -n kube-system --as=system:serviceaccount:{{ns}}:{{sa}})" = "no" ]
```

## Solution: imperative

```bash
kubectl -n {{ns}} create serviceaccount {{sa}}
kubectl -n {{ns}} create role viewer --verb=get,list,watch --resource=pods,deployments.apps
kubectl -n {{ns}} create rolebinding viewer-binding --role=viewer --serviceaccount={{ns}}:{{sa}}
```

## Solution: manifests

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{sa}}
  namespace: {{ns}}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: viewer
  namespace: {{ns}}
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: viewer-binding
  namespace: {{ns}}
subjects:
  - kind: ServiceAccount
    name: {{sa}}
    namespace: {{ns}}
roleRef:
  kind: Role
  name: viewer
  apiGroup: rbac.authorization.k8s.io
EOF
```
