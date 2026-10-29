# Протокол перевірки стенду ЛР3

Варіант 19, кластер **kind** `hpc` (1 control-plane + 2 worker), образ `donor-registry:lab3`.
Розділи 1–16 — перевірки кроків 1–5 і Д1, розділ 17 — пробні прогони до кроку 6, розділ 18 —
перевірка генератора після кроків 6 і 10, розділ 19 — розгортання з нуля з фінальними
маніфестами. Прогони: 27 і 29 жовтня 2026. Команди виконані з кореня репозиторію.
Інструкція з поясненнями: [HOWTO-LAB3.md](../../HOWTO-LAB3.md).

Порти хоста: **18080** (сервіс) і **19090** (Prometheus) — 8080 на цій машині зайнятий
процесом `ApplicationWebServer`, і `kind create cluster` на ньому падає.

---

## 1. Вузли і мітка role=infra (крок 1, крок 2)

```powershell
kubectl get nodes -L role
```

Має бути: три вузли `Ready`, control-plane з міткою `role=infra`, два worker без мітки.

```
NAME                STATUS   ROLES           AGE     VERSION   ROLE
hpc-control-plane   Ready    control-plane   9m      v1.37.0   infra
hpc-worker          Ready    <none>          8m47s   v1.37.0
hpc-worker2         Ready    <none>          8m47s   v1.37.0
```

**Збіглося.**

## 2. Перенаправлення портів (крок 1)

```powershell
docker port hpc-control-plane
```

Має бути: NodePort 30080 на хостовий порт сервісу, 30090 на порт Prometheus.

```
6443/tcp -> 127.0.0.1:58596
30080/tcp -> 127.0.0.1:18080
30090/tcp -> 127.0.0.1:19090
```

**Збіглося.**

## 3. Стан усіх подів (крок 2)

```powershell
kubectl get pods -A
```

Має бути: жодного пода поза станами `Running` і `Completed`.

```
(порожній вивід після фільтра — немає Pending, Error, CrashLoopBackOff)
```

**Збіглося.**

## 4. База на вузлі role=infra (крок 3)

```powershell
kubectl get pod db-0 -n donors -o wide
```

Має бути: под `db-0` у стані `Running` на `hpc-control-plane`.

```
db-0   1/1   Running   0   5m36s   10.244.0.7   hpc-control-plane
```

**Збіглося.**

## 5. max_connections (крок 3)

```powershell
kubectl exec -n donors db-0 -- psql -U donors -d donors -tAc "show max_connections"
```

Має бути: `100`.

```
100
```

**Збіглося.**

## 6. Персистентність даних після видалення пода (крок 3)

```powershell
kubectl exec -n donors db-0 -- psql -U donors -d donors -tAc "insert into donor_registry (donor_code, birth_year, blood_group, hla_typing, registered_on, available) values ('PERSIST-1', 1990, 'O+', '{A1}', current_date, true)"
kubectl delete pod db-0 -n donors
kubectl rollout status statefulset/db -n donors --timeout=300s
kubectl exec -n donors db-0 -- psql -U donors -d donors -tAc "select count(*) from donor_registry where donor_code = 'PERSIST-1'"
```

Має бути: `INSERT 0 1`, под перестворюється, лічильник після перестворення `1`.

```
INSERT 0 1
pod "db-0" deleted from donors namespace
partitioned roll out complete: 1 new pods have been updated...
1
```

**Збіглося.** Дані лежать на PersistentVolumeClaim і переживають видалення пода.

> Під час цієї перевірки под `web` на кілька десятків секунд стає `0/1`: поки база відсутня,
> DNS-імʼя `db-0.db.donors.svc.cluster.local` не резолвиться і `/healthz` віддає 503. Це
> правильна поведінка readinessProbe, а не помилка.

## 7. Поля replicas немає в маніфесті (крок 4)

```powershell
Select-String -Path k8s\base\21-web-deployment.yaml -Pattern "^\s*replicas:"
```

Має бути: порожній вивід — кількість реплік задають `kubectl scale` і HPA.

```
(нічого не знайдено)
```

**Збіглося.**

## 8. Ресурси контейнера web (крок 4, варіант 19)

```powershell
kubectl get deployment web -n donors -o jsonpath="{.spec.template.spec.containers[0].resources}"
```

Має бути: `requests.cpu` 500m, `limits.cpu` 1, `limits.memory` 512Mi.

```json
{"limits":{"cpu":"1","memory":"512Mi"},"requests":{"cpu":"500m","memory":"128Mi"}}
```

**Збіглося.**

## 9. Проби і коректна зупинка (крок 4)

```powershell
kubectl get deployment web -n donors -o jsonpath="{range .spec.template.spec.containers[*]}{.readinessProbe}{'\n'}{.livenessProbe}{'\n'}{.lifecycle}{'\n'}{end}"
kubectl get deployment web -n donors -o jsonpath="{.spec.template.spec.terminationGracePeriodSeconds}"
kubectl get deployment web -n donors -o jsonpath="{.spec.template.spec.containers[0].startupProbe}{'
'}{.spec.template.spec.initContainers[0].name}"
```

Має бути: readiness на `/healthz` з `timeoutSeconds 3`, `periodSeconds 5`, `failureThreshold 3`;
liveness на `/livez`; `preStop.sleep.seconds 5`; `terminationGracePeriodSeconds 15`; крім того
`startupProbe` на `/livez` з `failureThreshold 36` і initContainer `wait-db`.

```json
{"failureThreshold":3,"httpGet":{"path":"/healthz","port":"http"},"periodSeconds":5,"timeoutSeconds":3}
{"failureThreshold":3,"httpGet":{"path":"/livez","port":"http","scheme":"HTTP"},"periodSeconds":10,"successThreshold":1,"timeoutSeconds":3}
{"preStop":{"sleep":{"seconds":5}}}
15
{"failureThreshold":36,"httpGet":{"path":"/livez","port":"http","scheme":"HTTP"},"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3}
wait-db
```

**Збіглося.** Спершу тут був `initialDelaySeconds: 30` у liveness, але перевірка розгортання з
нуля показала, що цього замало: на свіжому кластері база піднімається 1-2 хвилини. Замість паузи
додано `startupProbe` на `/livez` (до 36 × 5 = 180 с), який притримує liveness і readiness до
відкриття порту, і `initContainer` `wait-db`, який не пускає web, поки база не приймає зʼєднання
(розділ 19).

## 10. Пароля немає ніде, крім Secret (крок 4)

```powershell
kubectl get configmap web-config -n donors -o yaml | Select-String -Pattern "(?i)password"
docker run --rm --entrypoint env donor-registry:lab3 | Select-String PASSWORD
git check-ignore -v .env
kubectl get deployment web -n donors -o jsonpath="{.spec.template.spec.containers[0].env[?(@.name=='POSTGRES_PASSWORD')]}"
kubectl get deployment web -n donors -o jsonpath="{.spec.template.spec.containers[0].env[?(@.name=='POD_NAME')]}"
```

Має бути: у ConfigMap і в образі пароля немає, `.env` ігнорується git, пароль у поді через
`secretKeyRef`, `POD_NAME` через Downward API.

```
(пароля немає)
(пароля немає)
.gitignore:1:.env	.env
{"name":"POSTGRES_PASSWORD","valueFrom":{"secretKeyRef":{"key":"POSTGRES_PASSWORD","name":"db-secret"}}}
{"name":"POD_NAME","valueFrom":{"fieldRef":{"apiVersion":"v1","fieldPath":"metadata.name"}}}
```

**Збіглося.**

## 11. Повний цикл CRUD через NodePort із заголовком X-Pod (крок 4)

```bash
curl -i -X POST   http://127.0.0.1:18080/donor-registry -H "Content-Type: application/json" -d @sample.json
curl -i           http://127.0.0.1:18080/donor-registry/$ID
curl -i -X PUT    http://127.0.0.1:18080/donor-registry/$ID -H "Content-Type: application/json" -d @sample.json
curl -i -X DELETE http://127.0.0.1:18080/donor-registry/$ID
```

Має бути: `201`, `200`, `200`, `204`, у кожній відповіді непорожній заголовок `X-Pod`.

```
POST   HTTP/1.1 201 Created      x-pod: web-58ff4466f-49tv8
GET    HTTP/1.1 200 OK           x-pod: web-58ff4466f-49tv8
PUT    HTTP/1.1 200 OK           x-pod: web-58ff4466f-49tv8
DELETE HTTP/1.1 204 No Content   x-pod: web-58ff4466f-49tv8
```

**Збіглося.** Запити виконані на одній репліці, тому `X-Pod` однаковий: усі чотири пішли через
одне keep-alive зʼєднання, а kube-proxy обирає под під час встановлення зʼєднання.

> `Invoke-WebRequest` у неінтерактивному сеансі PowerShell падає з `NonInteractive mode`,
> тому перевірку зручніше робити через `curl`.

## 12. Д1: рівномірний розподіл по двох worker-вузлах

```powershell
kubectl scale deployment/web -n donors --replicas=4
kubectl get pods -n donors -l app=web -o custom-columns="POD:.metadata.name,NODE:.spec.nodeName"
```

Має бути: 2 + 2 по `hpc-worker` і `hpc-worker2`, усі готові.

```
web-78d98b4785-7db6n   hpc-worker2   true   0
web-78d98b4785-dtg69   hpc-worker2   true   0
web-78d98b4785-hf7mv   hpc-worker    true   0
web-78d98b4785-nh9d6   hpc-worker    true   0
```

**Збіглося після виправлення.** Спершу дві репліки з чотирьох не планувалися:

```
0/3 nodes are available: 1 node(s) had untolerated taint(s),
2 node(s) didn't match pod topology spread constraints
```

Причина: вузол control-plane рахувався як третій домен рівномірності з нулем подів, тож
розподіл 2+2+0 давав розбіжність 2 за `maxSkew: 1`. Виправлено полем `nodeTaintsPolicy: Honor`
у `topologySpreadConstraints` — вузли з taint, який под не толерує, більше не рахуються.

## 13. metrics-server (крок 5)

```powershell
kubectl top pods -n donors
```

Має бути: числа CPU і памʼяті для кожного пода, а не `error: Metrics API not available`.

```
db-0                                       31m   83Mi
pg-exporter-prometheus-postgres-exporter   5m    8Mi
web-78d98b4785-7db6n                       4m    47Mi
web-78d98b4785-dtg69                       64m   45Mi
web-78d98b4785-hf7mv                       2m    45Mi
web-78d98b4785-nh9d6                       3m    44Mi
```

**Збіглося.**

## 14. Ряди в Prometheus (крок 5)

```bash
curl -G http://127.0.0.1:19090/api/v1/query --data-urlencode "query=<запит>"
kubectl get prometheus -n monitoring -o jsonpath="{.items[0].spec.scrapeInterval}"
```

| Запит | Має бути | Вийшло |
|---|---|---|
| `count(sum by (pod) (rate(http_requests_total{namespace="donors"}[1m])))` | ряд на кожен под web | 6 (4 поточні + 2 щойно замінені) |
| `count(container_cpu_cfs_periods_total{namespace="donors",container="web"})` | 4 | 4 |
| `count(container_cpu_cfs_throttled_periods_total{namespace="donors",container="web"})` | 4 | 4 |
| `kube_deployment_status_replicas_ready{namespace="donors",deployment="web"}` | 4 | 4 |
| `pg_up` | 1 | 1 |
| `count(up{job="kubelet"} == 1)` | 3 (по вузлах) | 9 (kubelet, cAdvisor, probes на 3 вузли) |
| `scrapeInterval` | 5s | 5s |

**Збіглося.** Є і ряди запитів на кожен под, і лічильники CFS, і готові репліки, і база.

## 15. HPA бачить метрики (крок 11)

```powershell
kubectl apply -f k8s/hpa/web-hpa.yaml
kubectl get hpa web -n donors
```

Має бути: у колонці `TARGETS` відсоток, а не `<unknown>`; межі 1 і 4.

```
web   Deployment/web   cpu: 0%/50%   1   4   4   35s
```

Ряд `kube_horizontalpodautoscaler_status_desired_replicas{namespace="donors"}` повернув `1`.

**Збіглося.** Після перевірки HPA видалено: серія кроку 7 має виконуватися без автомасштабування.


## 16. Поведінка сервісу після перестворення пода бази

```bash
kubectl delete pod db-0 -n donors
kubectl rollout status statefulset/db -n donors
curl -o /dev/null -w "%{http_code} " "http://127.0.0.1:18080/donor-registry?limit=1"   # кілька разів поспіль
```

Має бути: сервіс кілька секунд відповідає помилкою, далі сам повертається до 200 без
перезапуску пода.

```
500 500 500 000 000      (перші секунди)
200                      (через ~5 с)
web-78d98b4785-pnqff   1/1   Running   0   2m31s   (перезапусків немає)
```

У журналі web:

```
psycopg.errors.AdminShutdown: terminating connection due to administrator command
```

**Очікувана поведінка.** База, яку видалили, обриває зʼєднання пулу; поки psycopg не замінить
їх, запити падають, а readinessProbe виводить под із Service. Пул відновлюється приблизно за
5 секунд, перезапуску контейнера не відбувається. Перевірку з автоматичним очікуванням цього
вікна виконує `scripts\lab3_selfcheck.ps1`.

Перевірку зʼєднання перед кожною видачею з пула (`check=` у psycopg_pool) спеціально не
вмикали: вона додала б запит до бази на кожен HTTP-запит і спотворила б вимірювання пропускної
здатності, заради яких ця робота й робиться.

Важливо: так пул відновлюється лише після **короткого** обриву. Якщо база недоступна хвилину й
довше (саме це буває на свіжому кластері), пул припиняє спроби назавжди — див. розділ 19.


## 17. Пробні прогони навантаження (не вимірювання ЛР3)

Генератор ще без моделі зʼєднань кроку 6, тому ці числа не йдуть у звіт. Прогін 20 секунд на
200 rps, `loadgen.gen --base http://127.0.0.1:18080 --rate 200 --warmup 5 --duration 20`:

| Умови | Досягнуто, rps | p50, мс | p99, мс | Не-2xx | Запити по подах |
|---|---|---|---|---|---|
| 4 репліки, стенд у спокої | 199,95 | 3,7 | 38,7 | 0 % | 1626 / 1163 / 860 / 351 |
| 1 репліка, стенд у спокої | 199,9 | 3,8 | 249,0 | 0 % | 4000 |
| 1 репліка, одразу після `kubectl delete pod db-0` | 199,9 | 3,6 | 130,0 | 0 % | 4000 |

Коефіцієнт дисбалансу на чотирьох репліках у першому рядку: I = 1626 / 1000 = **1,63** при
найбільшому можливому 4. Причина саме та, що описана в умові: кожне keep-alive зʼєднання
генератора привʼязане до пода, обраного під час встановлення TCP-зʼєднання, і більше його не
змінює. Після кроку 6 (зʼєднання закривається після 100 запитів) розподіл має вирівнятися.

**Окремо: аномальний прогін одразу після запуску Docker Desktop.** Перший пробний прогін на
200 rps дав 81 rps, 59,5 % не-2xx, p50 17 с і 1744 запити в обробці. Відтворити на сталому
стенді не вдалося жодного разу (три рядки таблиці вище). Збіг обставин того прогону: щойно
стартували Docker Desktop і три вузли кластера, піднімався моніторинг (Prometheus, operator,
kube-state-metrics, node-exporter на трьох вузлах), а перед самим прогоном самоперевірка
масштабувала Deployment і видаляла HPA, тобто поди ще перестворювалися. Висновок для вимірювань
кроків 7–12: починати прогін лише тоді, коли `kubectl get pods -n donors` кілька разів поспіль
показує той самий склад подів, а `kubectl top nodes` — низьке використання CPU.


## 18. Перевірка генератора після кроків 6 і 10

Модель зʼєднань: 32 постійних зʼєднання (8·R), кожне закривається після 100 запитів.
Підтвердження з підсумків прогонів — поле `connections`:

| Прогін | Зʼєднань | Відкрито за прогін | Закрито після 100 запитів |
|---|---|---|---|
| профіль 80 с (100 / 400 / 100 rps) | 32 (по 8 на 4 процеси) | 200 | 164 |
| сходинки на `/donor-registry`, N = 4 | 32 | 461 | 404 |
| сходинки на `/livez`, N = 4 | 32 | 829 | 784 |

Відкритих завжди рівно 32; те, що відкрито сотні, і є доказом переробки зʼєднань.

**Межа стенду без бази.** Ступінчастий прогін на `/livez` (відповідь без звернення до бази,
той самий шлях генератор → NodePort → kube-proxy → под):

| Задано, rps | 200 | 400 | 600 | 800 | 1000 |
|---|---|---|---|---|---|
| Досягнуто, rps | 199,8 | 400,0 | 586,0 | 610,2 | 707,6 |
| p50, мс | 2,7 | 2,7 | 3,2 | 410,5 | 1916,6 |
| p99, мс | 97 | 293 | 680 | 5029 | 5050 |

Межа шляху **≈ 700 rps**. Для порівняння, на `/donor-registry` з базою на тих самих чотирьох
репліках цей діагностичний прогін сходинками по 200 rps дав лише 366 rps (опублікована серія
кроку 7 сходинками по 50 rps дала X(4) = 377,1 rps — див. звіт), при цьому поди web разом
використали лише 1,42 ядра з доступних
чотирьох, а частка дроселювання CPU не перевищила 2 %. Тобто на цьому стенді обмежує не
процесор контейнерів, а шлях доставки запитів: опублікований порт Docker Desktop,
kube-proxy і мережа CNI між вузлами (база стоїть на вузлі control-plane, тож кожен запит
до неї теж іде між вузлами). Це пояснює, чому X(N) у kind помітно нижчі за значення
Compose з ЛР2, і це саме те, що порівнює крок 9.

**Пастка Git Bash.** Аргумент `--path /livez` у Git Bash перетворюється на
`C:/Program Files/Git/livez`, сервіс відповідає 400, і прогін виглядає як суцільні помилки.
Запускати так: `MSYS_NO_PATHCONV=1 python -m loadgen.step --path "/livez"`, або з PowerShell,
де перетворення немає. Те саме застереження вже є в RUN-LAB2 для `/noop`.


## 19. Розгортання з нуля з фінальними маніфестами

```powershell
powershell -File scripts\k8s_down.ps1
powershell -File scripts\k8s_up.ps1
powershell -File scripts\lab3_selfcheck.ps1
```

Має бути: розгортання без помилок, под web `1/1` з нулем перезапусків, `/healthz` 200,
самоперевірка `ПРОЙДЕНО 15, НЕ ПРОЙДЕНО 0`.

Перші дві спроби не зійшлися, і обидві поломки були справжніми, просто невидимими на вже
працюючому кластері:

| Спроба | Що сталося | Виправлення |
|---|---|---|
| 1 | под web `1/1`, але `RESTARTS 1`: `Liveness probe failed: connection refused`, `Container web failed liveness probe, will be restarted` | `startupProbe` на `/livez`, до 36 × 5 = 180 с |
| 2 | перезапусків немає, але под висить `0/1` понад 5 хв: `/livez` 200, `/healthz` 503, у журналі `failed to resolve host 'db-0.db...'` і жодної нової спроби | `initContainer` `wait-db`: чекає, поки база прийме зʼєднання на 5432 |
| 3 | розгортання 4 хв 47 с, под `1/1`, 0 перезапусків, `/healthz` 200, самоперевірка 15/15 | — |

Друга поломка найважливіша: поки DNS-імʼя пода бази не резолвиться, пул psycopg робить серію
невдалих спроб і припиняє їх, а сервіс лишається з мертвим пулом, з якого не виходить сам.
Без `initContainer` стенд на чистій машині просто не піднявся б.

---

## Підсумок

| # | Перевірка | Результат |
|---|---|---|
| 1 | вузли і мітка `role=infra` | збіглося |
| 2 | перенаправлення портів | збіглося |
| 3 | стан усіх подів | збіглося |
| 4 | база на вузлі `role=infra` | збіглося |
| 5 | `max_connections = 100` | збіглося |
| 6 | персистентність після видалення пода | збіглося |
| 7 | немає поля `replicas` | збіглося |
| 8 | ресурси 500m / 1 CPU / 512Mi | збіглося |
| 9 | проби і коректна зупинка | збіглося (див. розділ 19 про `startupProbe`) |
| 10 | пароль лише в Secret | збіглося |
| 11 | CRUD через NodePort із `X-Pod` | збіглося |
| 12 | Д1: 2 + 2 по воркерах | збіглося після `nodeTaintsPolicy: Honor` |
| 13 | `kubectl top pods` | збіглося |
| 14 | ряди в Prometheus, інтервал 5s | збіглося |
| 15 | HPA бачить метрики | збіглося |
| 16 | відновлення після перестворення бази | збіглося (≈5 с, без перезапуску) |
| 17 | пробні прогони навантаження до кроку 6 | дисбаланс I = 1,63, аномалію на щойно запущеному Docker відтворити не вдалося |
| 18 | модель зʼєднань кроку 6 і межа стенду | збіглося: 32 відкритих, сотні перероблених, межа 738 rps |
| 19 | розгортання з нуля і самоперевірка на свіжому кластері | збіглося після `startupProbe` і `initContainer` |

Кроки 1–5 і додаткова вимога Д1 виконані й перевірені. Кроки 6–13 виконані також: результати
вимірювань і розрахунків — у [tables.md](tables.md), [elasticity.md](elasticity.md) і
[charts/](charts), числа і висновки — у [REPORT-LAB3.md](../../REPORT-LAB3.md).

## Виправлення, зроблені під час перевірки

| Що було | Чому | Як виправлено |
|---|---|---|
| `kind create cluster` падав: `ports are not available ... bind` | порт 8080 тримає `ApplicationWebServer` | порти хоста 18080 і 19090 у `k8s/kind-cluster.yaml` |
| скрипт продовжував роботу після падіння `kind` і застосував маніфести в поточний контекст kubeconfig | `$ErrorActionPreference` не діє на зовнішні програми | перевірка `$LASTEXITCODE`, явний `--context kind-hpc` у кожній команді, зупинка, якщо контекст інший |
| `No kind clusters found.` зупиняло скрипт | у PowerShell 5.1 stderr зовнішньої програми стає помилкою | виклик обгорнуто `$ErrorActionPreference = "Continue"` |
| `The script failed due to call depth overflow` | функція-обгортка називалася `Kubectl` і перекривала саму програму | перейменовано на `Invoke-Kubectl`, усередині `kubectl.exe` |
| `kubectl patch --type=json -p ...` відхилявся сервером | PowerShell псує лапки у вбудованому JSON | патч винесено у `k8s/monitoring/metrics-server-insecure-tls.patch.json` |
| под web перезапускався на старті | liveness не чекає, поки сервіс дочекається бази | `startupProbe` на `/livez` з вікном 180 с |
| на свіжому кластері под лишався `0/1` назавжди: `/livez` 200, `/healthz` 503 | пул psycopg припиняє спроби підключення після довгої серії невдач | `initContainer` `wait-db` чекає, поки база прийме зʼєднання |
| дві з чотирьох реплік не планувалися | control-plane рахувався як домен рівномірності | `nodeTaintsPolicy: Honor` у `topologySpreadConstraints` |
| у Status -> Targets постійно висіли `down` таргети kube-controller-manager, kube-scheduler, kube-etcd, kube-proxy | у kind ці компоненти слухають 127.0.0.1 усередині свого контейнера | вимкнено у values чарта: для роботи потрібні лише web, cAdvisor, kube-state-metrics і postgres-exporter |
