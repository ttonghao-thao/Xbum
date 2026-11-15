#!/usr/bin/env python3
"""Local operator commands. No network administration endpoint or model inference."""

import argparse
import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import httpx
from sqlalchemy.engine import make_url

import run as lifecycle
from app.core.config import settings
from app.services.system import model_status

ROOT = Path(__file__).resolve().parents[1]
CONFIRMATION = "RESET_XBUM"


def database_path():
    url = make_url(settings.database_url)
    if url.drivername != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("本地管理命令要求文件型 SQLite 数据库")
    return Path(url.database).resolve()


@contextmanager
def read_database():
    path = database_path()
    if not path.is_file():
        yield None
        return
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")  # One consistent WAL snapshot while the Worker runs.
        yield db
        db.rollback()


def safe_value(value):
    # Reuse the application's serialized-field policy, also exclude nested tokens
    # and all binary vectors. Import only after a database is known to exist.
    from app.db.models import SENSITIVE

    if isinstance(value, dict):
        return {
            key: safe_value(item)
            for key, item in value.items()
            if key not in SENSITIVE
            and "token" not in key.lower()
            and "password" not in key.lower()
            and key not in {"vector", "vectors"}
        }
    if isinstance(value, list):
        return [safe_value(item) for item in value]
    return value


def records(db, table):
    from app.db.models import SENSITIVE

    # Table names come exclusively from SQLite's catalog, never user SQL input.
    quoted = '"' + table.replace('"', '""') + '"'
    columns = {row["name"]: row["type"] for row in db.execute(f"PRAGMA table_info({quoted})")}
    selected = [
        key
        for key, kind in columns.items()
        if kind.upper() != "BLOB"
        and key not in SENSITIVE
        and "token" not in key.lower()
        and "password" not in key.lower()
        and not (table == "login_attempts" and key == "key")
    ]
    expressions = ['"' + key.replace('"', '""') + '"' for key in selected]
    if table == "users":
        expressions.append("CASE WHEN password_hash != '' THEN 1 ELSE 0 END AS password_set")
    if not expressions:
        return []
    result = []
    for row in db.execute(f"SELECT {', '.join(expressions)} FROM {quoted}"):
        item = {}
        for key, value in dict(row).items():
            if key == "password_set":
                continue
            if columns[key].upper() == "JSON" and value is not None:
                value = json.loads(value)
            item[key] = value
        item = safe_value(item)
        if table == "users":
            item["password_set"] = bool(row["password_set"])
        result.append(item)
    return result


def database_report(include_all=False):
    result = {"path": str(database_path()), "initialized": False, "counts": {}}
    with read_database() as db:
        if db is None:
            return result
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        result["initialized"] = {"users", "households", "photos", "alembic_version"} <= set(tables)
        result["integrity"] = db.execute("PRAGMA integrity_check").fetchone()[0]
        result["foreign_key_errors"] = len(db.execute("PRAGMA foreign_key_check").fetchall())
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            result["counts"][table] = db.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]
        if not result["initialized"]:
            return result
        result["revision"] = [row[0] for row in db.execute("SELECT version_num FROM alembic_version")]
        result["users"] = records(db, "users")
        memberships = records(db, "memberships")
        homes = records(db, "households")
        home_names = {h["id"]: h["name"] for h in homes}
        result["households"] = homes
        for user in result["users"]:
            user["memberships"] = [
                {**m, "household_name": home_names.get(m["household_id"])}
                for m in memberships
                if m["user_id"] == user["id"]
            ]
            user["account_state"] = (
                "active" if any(m["status"] == "active" for m in user["memberships"]) else "needs_household"
            )
            user["photo_count"] = db.execute(
                "SELECT count(*) FROM photos WHERE uploader_id=?", (user["id"],)
            ).fetchone()[0]
        result["photo_states"] = [
            dict(row)
            for row in db.execute(
                "SELECT visibility, is_hidden, is_deleted, count(*) AS total FROM photos GROUP BY visibility, is_hidden, is_deleted"
            )
        ]
        result["job_states"] = [
            dict(row) for row in db.execute("SELECT state, count(*) AS total FROM jobs GROUP BY state")
        ]
        if include_all:
            result["tables"] = {table: records(db, table) for table in tables}
    return result


def process_report():
    result = []
    for process in lifecycle.supervisors():
        try:
            result.append(
                {
                    "pid": process.pid,
                    "created": process.create_time(),
                    "children": [{"pid": p.pid, "name": p.name()} for p in process.children(recursive=True)],
                }
            )
        except lifecycle.psutil.NoSuchProcess:
            pass
    return {"running": bool(result), "supervisors": result}


def status(include_all=False):
    result = {"service": process_report(), "database": database_report(include_all)}
    try:
        response = httpx.get(f"http://127.0.0.1:{settings.app_port}/api/v1/system/health", timeout=1, trust_env=False)
        result["api"] = {"reachable": response.is_success, "status_code": response.status_code}
    except httpx.HTTPError:
        result["api"] = {"reachable": False}
    if settings.data_dir.is_dir():
        result["models"] = model_status()
    try:
        response = httpx.get(settings.ollama_base_url + "/api/ps", timeout=1, trust_env=False)
        response.raise_for_status()
        result["loaded_models"] = [m.get("name") for m in response.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        result["loaded_models"] = None
    return result


def print_summary(report):
    database = report["database"]
    print("Xbum 服务：" + ("运行中" if report["service"]["running"] else "未运行"))
    print("API：" + ("可访问" if report["api"]["reachable"] else "未响应"))
    print("数据目录：", settings.data_dir.resolve())
    print("数据库：", database["path"])
    if not database["initialized"]:
        print("数据库尚未初始化；运行 make start 后打开 /setup。")
        return
    print("数据库版本：", ", ".join(database["revision"]))
    print("完整性：", database["integrity"], "；外键错误：", database["foreign_key_errors"])
    print("各表记录数：", json.dumps(database["counts"], ensure_ascii=False))
    print("照片状态：", json.dumps(database["photo_states"], ensure_ascii=False))
    print("任务状态：", json.dumps(database["job_states"], ensure_ascii=False))
    print("账号：")
    for user in database["users"]:
        homes = ", ".join(f"{m['household_name']} ({m['role']}/{m['status']})" for m in user["memberships"])
        print(
            f"  {user['username']} / {user['display_name']}；{homes or '无家庭，登录后可创建或加入'}；照片 {user['photo_count']}"
        )
    print("模型与磁盘：", json.dumps(report.get("models", {}), ensure_ascii=False))
    print("已加载模型：", json.dumps(report["loaded_models"], ensure_ascii=False))
    print("完整脱敏记录：make status-all；账号：make users；照片：make photos")


def validate_reset_target():
    raw = settings.data_dir.absolute()
    root = raw.resolve()
    protected = [ROOT, Path.home()]
    if raw.is_symlink() or any(p.is_relative_to(root) for p in protected):
        raise ValueError("拒绝重置仓库、用户主目录、其上层目录或符号链接")
    for path in [ROOT / p for p in [".git", ".env", ".venv", "apps", "scripts", "docs", "backups"]] + [
        settings.model_dir.resolve(),
        settings.whisper_model.resolve(),
    ]:
        if root.is_relative_to(path) or path.is_relative_to(root):
            raise ValueError("DATA_DIR 与源码、配置、备份或模型路径重叠，拒绝重置")
    database = database_path()
    if not database.is_relative_to(root):
        raise ValueError("DATABASE_URL 必须在 DATA_DIR 内才能完整重置")
    if root.exists() and any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("DATA_DIR 含符号链接，拒绝重置")
    report = database_report()
    if root.exists() and any(p.name not in {"service.lock", "worker.lock", "maintenance.lock"} for p in root.iterdir()):
        if not report["initialized"]:
            raise ValueError("数据目录包含未识别的数据，拒绝作为 Xbum 重置")
    return root, database.relative_to(root), report


@contextmanager
def exclusive_service(root):
    with open(root / "service.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("仍有单独运行的 API/Worker；请在其原终端停止后重试") from None
        yield


def factory_reset(confirmation=None):
    root, relative_db, report = validate_reset_target()
    print("将清空应用数据目录：", root, flush=True)
    print("删除全部账号、家庭、会话、照片/录音/视频、分析结果、任务及审计。", flush=True)
    print("保留源码、.env、依赖、下载模型和数据目录外的备份/诊断文件。", flush=True)
    print("当前记录数：", json.dumps(report["counts"], ensure_ascii=False), flush=True)
    try:
        confirmation = confirmation if confirmation is not None else input(f"输入 {CONFIRMATION} 确认：")
    except EOFError:
        confirmation = ""
    if confirmation != CONFIRMATION:
        raise ValueError("未确认，未停止服务或修改数据")
    with lifecycle.locked("control.lock"):
        lifecycle.stop()
        # Validate again after stopping, before touching the target.
        root, relative_db, _ = validate_reset_target()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with exclusive_service(root):
            stage = Path(tempfile.mkdtemp(prefix="xbum-reset-", dir=root.parent))
            fresh, previous = stage / "fresh", stage / "previous"
            try:
                fresh.mkdir(mode=0o700)
                (fresh / relative_db).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with exclusive_service(fresh):
                    env = {
                        **os.environ,
                        "DATA_DIR": str(fresh),
                        "DATABASE_URL": "sqlite:///" + str(fresh / relative_db),
                    }
                    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True)
                    with sqlite3.connect(fresh / relative_db) as db:
                        if (
                            db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                            or db.execute("PRAGMA foreign_key_check").fetchall()
                        ):
                            raise ValueError("新数据库校验失败，原数据保留")
                    root.rename(previous)
                    try:
                        fresh.rename(root)
                    except OSError:
                        previous.rename(root)
                        raise
                    shutil.rmtree(previous)
            finally:
                # Never auto-delete old data after a failed rollback or cleanup.
                if previous.exists():
                    print(f"重置未完成，原数据或未清理文件保留于：{previous}", file=sys.stderr, flush=True)
                else:
                    shutil.rmtree(stage)
    print("恢复出厂设置完成，服务已停止。运行 make start，然后打开 /setup 重新创建账号和家庭。", flush=True)


def main():
    os.chdir(ROOT)
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Xbum 本地开发管理命令")
    commands = parser.add_subparsers(dest="command", required=True)
    summary = commands.add_parser("status")
    summary.add_argument("--all", action="store_true", help="包含所有表的完整脱敏记录")
    summary.add_argument("--json", action="store_true")
    commands.add_parser("users")
    commands.add_parser("photos")
    reset = commands.add_parser("factory-reset")
    reset.add_argument("--confirm", help=f"自动化显式确认值：{CONFIRMATION}")
    args = parser.parse_args()
    try:
        if args.command == "factory-reset":
            factory_reset(args.confirm)
        elif args.command == "status":
            report = status(args.all)
            if args.json or args.all:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print_summary(report)
        else:
            report = database_report(include_all=args.command == "photos")
            items = report.get("users", []) if args.command == "users" else report.get("tables", {}).get("photos", [])
            print(json.dumps({"items": items, "total": len(items)}, ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error, subprocess.CalledProcessError, lifecycle.psutil.Error) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
