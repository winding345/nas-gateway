#!/usr/bin/env python3
# ══════════════════════════════════════════════════════════════
#  nas-gateway —— 用户管理命令行工具
#
#  日常请在管理页操作（http://<NAS>:9092）
#  这个 CLI 用于：应急、脚本化、或管理页还没起来时
#
#  用法（在容器里跑，或用 scripts/add-user.sh 包装）：
#    python cli.py list
#    python cli.py add <用户名> <组>          # 密码从标准输入读
#    python cli.py passwd <用户名>            # 密码从标准输入读
#    python cli.py delete <用户名>
# ══════════════════════════════════════════════════════════════
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

import yaml
from argon2 import PasswordHasher
from argon2.low_level import Type

PH = PasswordHasher(
    time_cost=3, memory_cost=65536, parallelism=4,
    hash_len=32, salt_len=16, type=Type.ID,
)

ROOT = Path(os.environ.get("GATEWAY_ROOT", "/gateway")).resolve()
USERS_FILE = ROOT / "authelia" / "users_database.yml"
GROUPS = ("admins", "users")


def load() -> dict:
    if not USERS_FILE.exists():
        return {}
    data = yaml.safe_load(USERS_FILE.read_text(encoding="utf-8")) or {}
    users = data.get("users") or {}
    return users if isinstance(users, dict) else {}


def save(users: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        {"users": users}, allow_unicode=True, sort_keys=True, default_flow_style=False
    )
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, USERS_FILE)
    try:
        os.chmod(USERS_FILE, 0o600)
    except OSError:
        pass


def read_password() -> str:
    if sys.stdin.isatty():
        p1 = getpass.getpass("密码（至少 8 位）: ")
        p2 = getpass.getpass("再输一次: ")
        if p1 != p2:
            sys.exit("❌ 两次输入不一致")
    else:
        p1 = sys.stdin.readline().rstrip("\n")
    if len(p1) < 8:
        sys.exit("❌ 密码至少 8 位")
    return p1


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    cmd = args[0]

    if cmd == "list":
        users = load()
        if not users:
            print("（还没有用户）")
            return
        print(f"{'用户名':<20} {'组':<16} 显示名")
        print("-" * 52)
        for name, u in sorted(users.items()):
            groups = ",".join(u.get("groups") or [])
            print(f"{name:<20} {groups:<16} {u.get('displayname') or ''}")
        return

    if cmd == "add":
        if len(args) < 3:
            sys.exit("用法: cli.py add <用户名> <组>   （组：admins|users）")
        name, group = args[1], args[2]
        if group not in GROUPS:
            sys.exit(f"❌ 组只能是 {' 或 '.join(GROUPS)}")
        users = load()
        if name in users:
            sys.exit(f"❌ 用户 {name} 已存在")
        pwd = read_password()
        users[name] = {
            "displayname": name,
            "password": PH.hash(pwd),
            "email": "",
            "groups": [group],
        }
        save(users)
        print(f"✅ 已创建用户 {name}（组：{group}）")
        return

    if cmd == "passwd":
        if len(args) < 2:
            sys.exit("用法: cli.py passwd <用户名>")
        name = args[1]
        users = load()
        if name not in users:
            sys.exit(f"❌ 用户 {name} 不存在")
        users[name]["password"] = PH.hash(read_password())
        save(users)
        print(f"✅ 已更新 {name} 的密码")
        return

    if cmd == "delete":
        if len(args) < 2:
            sys.exit("用法: cli.py delete <用户名>")
        name = args[1]
        users = load()
        if name not in users:
            sys.exit(f"❌ 用户 {name} 不存在")
        admins = sum(1 for u in users.values() if "admins" in (u.get("groups") or []))
        if "admins" in (users[name].get("groups") or []) and admins <= 1:
            sys.exit("❌ 不能删除最后一个 admins 用户")
        del users[name]
        save(users)
        print(f"✅ 已删除用户 {name}")
        return

    sys.exit(f"❌ 未知命令：{cmd}")


if __name__ == "__main__":
    main()
