---
id: topic-001                  # same as the file name (without .md)
domain: services-networking    # application-design-build | application-deployment |
                               # application-observability-maintenance |
                               # application-environment-config-security | services-networking
topic: network-policy
difficulty: 1                  # 1 easy, 2 medium, 3 hard (harder than the real exam)
points: 4                      # must equal the sum of the points in "Check"
namespaces: ["{{ns}}"]         # the validator DELETES these namespaces between runs
check_timeout: 60              # optional: seconds the checks wait for the state to converge
vars:                          # optional: drawn per session, used as {{name}}
  ns: [alpha, beta]
---

## Statement

Task text in Markdown, as in the real exam. You can use {{ns}}.

## Setup

```bash
kubectl create namespace {{ns}}
```

## Check

One line per check, in the format `points|description|command`. The command must exit with
code 0 when the answer is right. Prefer checking the outcome (state or behavior), not the
way the student solved it.

```
2|the resource exists|kubectl -n {{ns}} get pod example
2|the resource has the right field|[ "$(kubectl -n {{ns}} get pod example -o jsonpath='{.spec.x}')" = "y" ]
```

## Solution: imperative

```bash
kubectl -n {{ns}} run example --image=nginx
```

## Solution: declarative

Two different solutions that pass the same Check prove the Check is not brittle.

```bash
kubectl apply -f - <<EOF
...
EOF
```
