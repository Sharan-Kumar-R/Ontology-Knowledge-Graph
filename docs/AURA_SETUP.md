# Connecting the pipeline to Neo4j Aura

Running the graph on Aura's free cloud tier. Nothing to install: no Docker, no
Java, no admin rights.

There is no "connect" command. The connection **is** `config/settings.yaml`;
every command reads it.

---

## TL;DR

```powershell
git pull
# edit config\settings.yaml  (uri + user + password)
.\.venv\Scripts\python.exe -m kg.cli check
.\.venv\Scripts\python.exe -m kg.cli run-all --batch-size 500
```

Then open **console.neo4j.io** → your instance → **Query**.

---

## 1. Create the instance

1. Go to **console.neo4j.io** and sign in.
2. **Instances → Create instance → Free.**
3. Copy the credentials it shows. **The password is displayed once.** Download
   the credentials file — it is the easiest way not to lose it.
4. Wait for the status to read **RUNNING**, roughly a minute.

Lost the password? Instance `...` menu → **Recover Database Credentials**. It
generates a new one; there is no way to retrieve the old.

In that menu, avoid **Reset To Blank** (wipes the data) and **Delete**
(destroys the instance).

## 2. Install the project

Only needed once per machine:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
copy config\settings.yaml.example config\settings.yaml
```

`config/settings.yaml` is gitignored, so a fresh clone never has it. This is
the step people miss.

## 3. Fill in the connection

Edit `config\settings.yaml`:

```yaml
data_root: C:/kg-data
neo4j_uri: neo4j+s://<instance-id>.databases.neo4j.io
neo4j_user: neo4j
neo4j_password: <the generated password>
```

Three things that are not obvious:

- **`neo4j_user` is not always `neo4j`.** Some instances use the instance id
  (an 8-character hex string) as both the username and the database name. If
  you get `AuthError` with a password you are certain of, try the instance id.
- **`data_root` must be writable.** It only holds generated Parquet. Point it
  under your user profile if the root of `C:` is locked down.
- **Everything else stays default.** No code changes anywhere.

## 4. Prove the connection

```powershell
.\.venv\Scripts\python.exe -m kg.cli check
```

Success looks like:

```
data_root:    C:\kg-data
sample files: 50 JSON under data\samples
{
  "neo4j_version": "5.27-aura",
  "n10s_available": false
}
```

`n10s_available: false` is expected and harmless. Nothing in the pipeline calls
n10s; the field is only a capability report.

## 5. Build the graph

```powershell
.\.venv\Scripts\python.exe -m kg.cli run-all --batch-size 500
```

`run-all` is `parse` → `resolve` → `load` → `stats`. Expect:

```
loaded 14075 mentions, 13901 edges
loaded 25 entities, 73 resolution edges
```

**Keep `--batch-size 500`.** The 5000 default is sized for a local instance;
against Aura Free the write outgrows the connection and the driver raises
`SessionExpired` partway through, leaving nothing loaded.

## 6. Look at it

Console → your instance → **Query**. Queries to start with are in
[GRAPH_COMMANDS.md](GRAPH_COMMANDS.md).

```cypher
MATCH p=(parent:Mention {mention_type:'LegalEntity'})-[:PARENT_OF]->(sub)
WHERE parent.name CONTAINS 'Apple'
RETURN p
```

---

## When port 7687 is blocked

Corporate networks commonly permit only 80 and 443. The bolt protocol needs
**7687**, so `check` fails with:

```
ServiceUnavailable: Unable to retrieve routing information
```

Aura serves the same database over an HTTP API on **443** — the port your
browser already uses to reach the console. Change only the scheme:

```yaml
neo4j_uri: https://<instance-id>.databases.neo4j.io
```

Then run `check` and `run-all --batch-size 500` exactly as before. The URI
scheme selects the transport in
[`get_driver`](../src/kg/load/neo4j_conn.py); nothing else in the pipeline
knows the difference.

| URI scheme | Transport | Port |
|---|---|---|
| `neo4j+s://`, `bolt://` | bolt driver | 7687 |
| `https://` | HTTP Query API | 443 |

Two limits over HTTP:

- **`validate` without `--offline` is unsupported.** It reads whole node
  objects, which the HTTP API serialises differently. Use
  `validate --offline --limit 0` — same shapes, same result, no database.
- **It is slower.** One request per batch instead of a persistent connection.

`https://` works on unrestricted networks too, so you can leave it set
everywhere rather than switching per machine.

---

## Reading the failure

The error tells you how far the connection got.

| Error | Meaning | Fix |
|---|---|---|
| `ServiceUnavailable: Unable to retrieve routing information` | never reached the server | switch the URI to `https://` |
| `AuthError: ... Unauthorized` | reached the server, credentials rejected | wrong password, or the user is the instance id |
| `Database does not exist. Database name: 'neo4j'` | wrong database name | the database is named after the instance; set `neo4j_user` to it |
| `SessionExpired: Failed to write data to connection` | batch too large | `--batch-size 500` |
| `FileNotFoundError: config/settings.yaml` | config not created | `copy config\settings.yaml.example config\settings.yaml` |
| `FileNotFoundError: data/samples` | wrong working directory | run from the project root |

`AuthError` is good news: it proves the network path works and only the secret
is wrong.

To test the port directly:

```powershell
Test-NetConnection <instance-id>.databases.neo4j.io -Port 7687
```

`TcpTestSucceeded : False` means blocked — use `https://`.

---

## If nothing can reach the database

Extraction, resolution and the full SHACL quality gate run with no database at
all:

```powershell
.\.venv\Scripts\python.exe -m kg.cli parse
.\.venv\Scripts\python.exe -m kg.cli resolve
.\.venv\Scripts\python.exe -m kg.cli validate --offline --limit 0
```

You still lose nothing on the viewing side: the Aura console is plain HTTPS on
443, and it talks to the database from Neo4j's servers rather than from your
machine. So a graph loaded from any machine stays browsable from a locked-down
one.

**Viewing needs 443. Loading needs 7687, or `https://`.**

---

## Things worth knowing

- **The free tier pauses after ~3 days idle.** One click resumes it; the data
  survives. Left paused long enough, it is deleted.
- **Limits are 200k nodes and 400k relationships.** This graph uses 14,000 and
  13,904 — about 7% of the node budget.
- **The password never enters git.** `config/settings.yaml` is gitignored, and
  only `settings.yaml.example` is tracked.
- **The graph lives in the cloud, not in the clone.** Load it from one machine
  and every other machine sees it, including through the browser.
