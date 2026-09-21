---
id: cronjob-001
domain: application-design-build
topic: cronjob
difficulty: 1
points: 6
namespaces: ["{{ns}}"]
vars:
  ns: [jobs, batch, cron]
  name: [backup, cleanup, report]
  schedule: ["*/5 * * * *", "0 3 * * *", "30 6 * * 1"]
---

## Statement

In namespace `{{ns}}`, create a CronJob named `{{name}}` with:

- schedule `{{schedule}}`
- image `busybox:1.36`, running the command `echo hello`
- `successfulJobsHistoryLimit` set to 2
- pod `restartPolicy` set to `OnFailure`

## Setup

```bash
kubectl create namespace {{ns}}
```

## Check

```
1|CronJob {{name}} exists|kubectl -n {{ns}} get cronjob {{name}}
1|schedule is "{{schedule}}"|[ "$(kubectl -n {{ns}} get cronjob {{name}} -o jsonpath='{.spec.schedule}')" = "{{schedule}}" ]
1|image is busybox:1.36|[ "$(kubectl -n {{ns}} get cronjob {{name}} -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}')" = "busybox:1.36" ]
1|runs echo hello|kubectl -n {{ns}} get cronjob {{name}} -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].command} {.spec.jobTemplate.spec.template.spec.containers[0].args}' | grep -q 'echo.*hello'
1|successfulJobsHistoryLimit = 2|[ "$(kubectl -n {{ns}} get cronjob {{name}} -o jsonpath='{.spec.successfulJobsHistoryLimit}')" = "2" ]
1|restartPolicy = OnFailure|[ "$(kubectl -n {{ns}} get cronjob {{name}} -o jsonpath='{.spec.jobTemplate.spec.template.spec.restartPolicy}')" = "OnFailure" ]
```

## Solution: imperative

```bash
kubectl -n {{ns}} create cronjob {{name}} --image=busybox:1.36 --schedule="{{schedule}}" -- echo hello
kubectl -n {{ns}} patch cronjob {{name}} --type=merge \
  -p '{"spec":{"successfulJobsHistoryLimit":2,"jobTemplate":{"spec":{"template":{"spec":{"restartPolicy":"OnFailure"}}}}}}'
```

## Solution: declarative

```bash
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{name}}
  namespace: {{ns}}
spec:
  schedule: "{{schedule}}"
  successfulJobsHistoryLimit: 2
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: {{name}}
              image: busybox:1.36
              command: ["echo", "hello"]
EOF
```
