"""Portal admin CLI (staff accounts exist only through this command).

  docker compose -p nox-portal exec api python manage.py create-staff --email ops@example.kz [--name "Имя"]
  docker compose -p nox-portal exec api python manage.py set-staff-password --email ops@example.kz
  docker compose -p nox-portal exec api python manage.py disable-staff --email ops@example.kz
  docker compose -p nox-portal exec api python manage.py enable-staff --email ops@example.kz
  docker compose -p nox-portal exec api python manage.py list-staff

The password is prompted (hidden, twice); for automation it may come from stdin with --password-stdin.
"""

import argparse
import asyncio
import getpass
import sys

import asyncpg

from nox_portal import db
from nox_portal.config import get_settings
from nox_portal.schemas import _email
from nox_portal.security import hash_password_sync, validate_password


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Пароль (не короче 10 символов): ")
        if getpass.getpass("Повторите пароль: ") != password:
            raise SystemExit("пароли не совпадают")
    try:
        return validate_password(password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


async def _run(args: argparse.Namespace) -> int:
    pool = await db.init_pool(get_settings().database_url)
    try:
        await db.migrate(pool)
        if args.cmd == "list-staff":
            for r in await pool.fetch("SELECT id, email, name, disabled, last_login_at FROM staff ORDER BY id"):
                state = "disabled" if r["disabled"] else "active"
                print(f"{r['id']:>4}  {r['email']:<40} {state:<9} {r['name']}  last login: {r['last_login_at'] or '-'}")
            return 0
        email = _email(args.email)
        if args.cmd == "create-staff":
            password_hash = hash_password_sync(_read_password(args.password_stdin))
            try:
                await pool.execute("INSERT INTO staff(email, name, password_hash) VALUES ($1, $2, $3)",
                                   email, args.name or "", password_hash)
            except asyncpg.UniqueViolationError:
                raise SystemExit(f"сотрудник {email} уже существует (смена пароля: set-staff-password)") from None
            await pool.execute("INSERT INTO audit_log(action, details) VALUES ('cli.create_staff', $1)",
                               {"email": email})
            print(f"ok: сотрудник {email} создан")
            return 0
        if args.cmd == "set-staff-password":
            password_hash = hash_password_sync(_read_password(args.password_stdin))
            sql = ("UPDATE staff SET password_hash = $2, failed_logins = 0, locked_until = NULL"
                   " WHERE email = $1 RETURNING id")
            values = (email, password_hash)
        else:
            sql = "UPDATE staff SET disabled = $2 WHERE email = $1 RETURNING id"
            values = (email, args.cmd == "disable-staff")
        if await pool.fetchval(sql, *values) is None:
            raise SystemExit(f"сотрудник {email} не найден")
        await pool.execute("INSERT INTO audit_log(action, details) VALUES ($1, $2)",
                           "cli." + args.cmd.replace("-", "_"), {"email": email})
        print(f"ok: {args.cmd} {email}")
        return 0
    finally:
        await db.close_pool()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="manage.py", description="Nox portal admin CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("create-staff", "set-staff-password", "disable-staff", "enable-staff"):
        p = sub.add_parser(name)
        p.add_argument("--email", required=True)
        if name == "create-staff":
            p.add_argument("--name", default="")
        if name in ("create-staff", "set-staff-password"):
            p.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    sub.add_parser("list-staff")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    sys.exit(main())
