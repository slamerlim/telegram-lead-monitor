"""Install native per-user services on Ubuntu. No sudo, Docker, or data deletion.

Run after committing and pushing: .venv/bin/python scripts/native_setup.py
Existing database/session directories are reused, never initialized over.
"""
from __future__ import annotations

import getpass
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

from dotenv import dotenv_values, set_key
import psycopg2
from psycopg2 import sql

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / ".local/state/telegram-lead-monitor"
PREFIX = Path.home() / ".local/opt/tlm-postgresql"
BIN = PREFIX / "usr/lib/postgresql/16/bin"
LIB = PREFIX / "usr/lib/x86_64-linux-gnu"
UNITS = Path.home() / ".config/systemd/user"
PG_PORT = 55432
REDIS_PORT = 56379


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def private_write(path: Path, contents: str):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(contents)
    path.chmod(0o600)


def unit(name: str, command: str, *, depends: bool = True, condition: str = ""):
    dependencies = "After=tlm-postgres.service tlm-redis.service\nWants=tlm-postgres.service tlm-redis.service\n" if depends else ""
    private_write(UNITS / f"tlm-{name}.service", f"""[Unit]
Description=Telegram Lead Monitor {name}
{dependencies}{condition}
[Service]
Type=simple
WorkingDirectory={ROOT}
EnvironmentFile={ROOT}/.env
EnvironmentFile=-{ROOT}/.env.validation
Environment=PYTHONUNBUFFERED=1
Environment=LD_LIBRARY_PATH={LIB}
ExecStart={command}
Restart=on-failure
RestartSec=10
TimeoutStopSec=45
UMask=0077
NoNewPrivileges=true
LogRateLimitIntervalSec=30s
LogRateLimitBurst=100

[Install]
WantedBy=default.target
""")


def main():
    os.chdir(ROOT)
    for directory in [STATE, STATE / "config", STATE / "socket", STATE / "redis", STATE / "telegram"]:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    if not (BIN / "postgres").exists():
        cache = Path.home() / ".cache/tlm-postgres-packages"
        cache.mkdir(parents=True, exist_ok=True)
        run(["apt-get", "download", "postgresql-16", "postgresql-client-16", "libpq5"], cwd=cache)
        for package in cache.glob("*.deb"):
            run(["dpkg-deb", "-x", package, PREFIX])
    if not shutil.which("redis-server"):
        raise SystemExit("redis-server is required; install the Ubuntu Redis package before proceeding")

    envfile = ROOT / ".env"
    current = dotenv_values(envfile)
    marker = STATE / "config/native-configured"
    if not marker.exists():
        if envfile.exists():
            backup = STATE / "config/env-before-native"
            if not backup.exists():
                private_write(backup, envfile.read_text())
        overrides = {
            "APP_ENV": "production", "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": str(PG_PORT), "POSTGRES_DB": "telegram_leads",
            "POSTGRES_USER": "telegram_leads", "POSTGRES_PASSWORD": secrets.token_hex(32),
            "REDIS_URL": f"redis://127.0.0.1:{REDIS_PORT}/0",
            "TELEGRAM_SESSION_PATH": str(STATE / "telegram/monitor.session"),
            "SCORING_CONFIG": str(ROOT / "config/scoring.yaml"),
            "AI_VALIDATORS_CONFIG": str(ROOT / "config/ai_validators.yaml"),
            "COMMERCIAL_AI_REVIEWERS_CONFIG": str(ROOT / "config/commercial_ai_reviewers.yaml"),
            "SCHEDULER_INTERVAL_SECONDS": "120", "SCHEDULER_SCAN_DAYS": "2",
            "SCAN_DEFAULT_DAYS": "2", "STORAGE_DATA_PATH": str(STATE),
            "STORAGE_MIN_FREE_GB": "10", "STORAGE_MAX_DATABASE_GB": "8",
        }
        for key, value in overrides.items():
            set_key(str(envfile), key, value, quote_mode="always")
        envfile.chmod(0o600)
        private_write(marker, "native Ubuntu user services\n")
    current = dotenv_values(envfile)

    # The owner explicitly requested Telegram alerts. Default to Saved Messages;
    # an existing chosen destination is preserved on every redeploy.
    for key, value in {"OWNER_NOTIFICATIONS_ENABLED": "true", "OWNER_TELEGRAM_TARGET": "me",
                       "HOT_LEAD_MAX_AGE_HOURS": "48"}.items():
        if key not in current:
            set_key(str(envfile), key, value, quote_mode="always")
    operators = {v.strip() for v in (current.get("COMMERCIAL_OPERATOR_IDS") or "").split(",") if v.strip()}
    operators.add("owner")
    set_key(str(envfile), "COMMERCIAL_OPERATOR_IDS", ",".join(sorted(operators)), quote_mode="always")
    set_key(str(envfile), "COMMERCIAL_OPS_ENABLED", "true", quote_mode="always")
    if not current.get("LABEL_WRITE_TOKEN"):
        set_key(str(envfile), "LABEL_WRITE_TOKEN", secrets.token_hex(32), quote_mode="always")
    envfile.chmod(0o600)

    if current.get("COMMERCIAL_DISCOVERY_VERSION") in (None, "disc_v6"):
        set_key(str(envfile), "COMMERCIAL_DISCOVERY_VERSION", "disc_v7", quote_mode="always")

    validation = ROOT / ".env.validation"
    if validation.exists():
        set_key(str(validation), "AI_VALIDATORS_CONFIG", str(ROOT / "config/ai_validators.yaml"), quote_mode="always")
        validation.chmod(0o600)

    native_env = dict(os.environ, LD_LIBRARY_PATH=str(LIB))
    data = STATE / "postgres"
    if not (data / "PG_VERSION").exists():
        if data.exists() and any(data.iterdir()):
            raise SystemExit("Nonempty PostgreSQL directory without PG_VERSION; refusing to overwrite")
        password_file = STATE / "config/postgres-bootstrap-password"
        if not password_file.exists():
            private_write(password_file, secrets.token_hex(32) + "\n")
        run([BIN / "initdb", "-D", data, "-U", getpass.getuser(),
             "--auth-local=peer", "--auth-host=scram-sha-256", "--encoding=UTF8",
             "--locale=C.UTF-8", "--data-checksums", f"--pwfile={password_file}"], env=native_env)
    private_write(STATE / "config/postgres.conf", f"""data_directory = '{data}'
hba_file = '{data}/pg_hba.conf'
ident_file = '{data}/pg_ident.conf'
listen_addresses = '127.0.0.1'
port = {PG_PORT}
unix_socket_directories = '{STATE / 'socket'}'
shared_buffers = '128MB'
max_wal_size = '512MB'
min_wal_size = '64MB'
log_statement = 'none'
logging_collector = off
""")
    private_write(STATE / "config/redis.conf", f"""bind 127.0.0.1
protected-mode yes
port {REDIS_PORT}
dir {STATE / 'redis'}
appendonly yes
appendfsync always
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
maxmemory 512mb
maxmemory-policy noeviction
save ""
loglevel warning
""")
    unit("postgres", f"{BIN}/postgres -c config_file={STATE}/config/postgres.conf", depends=False)
    unit("redis", f"/usr/bin/redis-server {STATE}/config/redis.conf", depends=False)
    python = ROOT / ".venv/bin/python"
    unit("api", f"{python} -m uvicorn services.api.app.main:app --host 127.0.0.1 --port 8010")
    modules = {"dispatcher": "dispatcher", "analyzer": "analyzer", "scheduler": "scheduler",
               "collector": "collector", "commercial-ai": "commercial_ai",
               "commercial-discovery": "commercial_discovery"}
    for name, module in modules.items():
        condition = f"ConditionPathExists={STATE}/telegram/authorized\n" if name == "collector" else ""
        unit(name, f"{python} -m services.{module}.app.main", condition=condition)
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "--now", "tlm-postgres", "tlm-redis"])
    for _ in range(60):
        check = subprocess.run([str(BIN / "pg_isready"), "-h", str(STATE / "socket"), "-p", str(PG_PORT)],
                               env=native_env, capture_output=True)
        if check.returncode == 0: break
        time.sleep(.5)
    else: raise SystemExit("PostgreSQL did not become ready; inspect tlm-postgres journal")

    admin = psycopg2.connect(dbname="postgres", user=getpass.getuser(), host=str(STATE / "socket"), port=PG_PORT)
    try:
        admin.autocommit = True
        with admin.cursor() as cursor:
            role = current["POSTGRES_USER"]
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(role)),
                               (current["POSTGRES_PASSWORD"],))
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (current["POSTGRES_DB"],))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(current["POSTGRES_DB"]), sql.Identifier(role)))
    finally:
        admin.close()
    run([python, "-m", "alembic", "upgrade", "head"], cwd=ROOT)
    services = ["tlm-api", *[f"tlm-{name}" for name in modules]]
    run(["systemctl", "--user", "enable", *services])
    run(["systemctl", "--user", "restart", *services])
    print("Native services installed. Production data path:", STATE)
    print("Telegram login: .venv/bin/python scripts/telegram_login.py")
    print("Health: http://127.0.0.1:8010/health")


if __name__ == "__main__":
    main()
