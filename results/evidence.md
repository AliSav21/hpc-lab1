# Докази

## Контейнери, порти і стан здоровʼя (публікує порт лише lb)
`docker compose ps --format table {{.Name}}	{{.Status}}	{{.Ports}}`

```
NAME           STATUS                    PORTS
donors-db-1    Up 31 minutes (healthy)   5432/tcp
donors-lb-1    Up 4 minutes              0.0.0.0:18080->80/tcp, [::]:18080->80/tcp
donors-web-1   Up 31 minutes (healthy)   8000/tcp
donors-web-2   Up 7 seconds (healthy)    8000/tcp
```

## Розмір фінального образу
`docker image inspect donor-registry:latest --format {{.Size}} байт`

```
65604397 байт
```

## Шари образу
`docker image history donor-registry:latest --format {{.Size}}	{{.CreatedBy}}`

```
0B	CMD ["uvicorn" "app.main:app" "--host" "0.0.…
0B	EXPOSE [8000/tcp]
0B	USER 10001
20.5kB	COPY app ./app # buildkit
4.1kB	WORKDIR /srv
79.8MB	COPY /opt/venv /opt/venv # buildkit
41kB	RUN /bin/sh -c useradd --system --uid 10001 …
0B	ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/l…
0B	CMD ["python3"]
16.4kB	RUN /bin/sh -c set -eux;  for src in idle3 p…
41.4MB	RUN /bin/sh -c set -eux;   savedAptMark="$(a…
0B	ENV PYTHON_SHA256=5c8462af5790baf43a321a1559…
0B	ENV PYTHON_VERSION=3.12.14
0B	ENV GPG_KEY=7169605F62C751356D054A26A821E680…
4.95MB	RUN /bin/sh -c set -eux;  apt-get update;  a…
0B	ENV LANG=C.UTF-8
0B	ENV PATH=/usr/local/bin:/usr/local/sbin:/usr…
87.6MB	# debian.sh --arch 'amd64' out/ 'trixie' '@1…
```

## Процес у контейнері: uid і користувач
`docker exec donors-web-1 id`

```
uid=10001(app) gid=999(app) groups=999(app)
```

## curl, wget, gcc в образі відсутні (додаткова вимога: healthcheck без curl/wget)
`docker exec donors-web-1 sh -c for c in curl wget gcc; do command -v $c || echo "$c: немає"; done`

```
curl: немає
wget: немає
gcc: немає
```

## Healthcheck сервісу (команда і стан)
`docker inspect donors-web-1 --format {{json .Config.Healthcheck.Test}} => {{.State.Health.Status}}`

```
["CMD","python","-c","import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"] => healthy
```

## Обмеження ресурсів web
`docker inspect donors-web-1 --format NanoCpus={{.HostConfig.NanoCpus}} Memory={{.HostConfig.Memory}}`

```
NanoCpus=1000000000 Memory=536870912
```

## Пароль не в образі, не в історії збірки, не в репозиторії
```
у history образу: не знайдено
у змінних образу: не знайдено
у файлах репозиторію (git ls-files): не знайдено
```

## .env не комітиться
`git check-ignore -v .env`

```
.gitignore:1:.env	.env
```

## Порти бази і вебінстансів з хоста недоступні, балансувальник доступний
```
127.0.0.1:5432 (Postgres): підключення немає
127.0.0.1:8000 (web):      підключення немає
127.0.0.1:18080 (lb):       доступно
```

## База доступна з контейнера сервісу за іменем db
`docker exec donors-web-1 python -c import socket;s=socket.create_connection(('db',5432),2);print('db:5432 підключення є')`

```
db:5432 підключення є
```

## Репліки: скільки вебінстансів зараз
`docker compose ps web --format {{.Name}}`

```
donors-web-1
donors-web-2
```
