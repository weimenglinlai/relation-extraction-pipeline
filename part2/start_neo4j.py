# -*- coding: utf-8 -*-
"""
启动本机 Neo4j。

为什么不用官方脚本：这套 5.3 发行版的 bin\\neo4j.bat 是 PowerShell 5.1 包装的，
在本机执行会报「已添加了具有相同键的项」（Neo4j-Management 模块重复导入的已知问题），
而 cmd.exe 又在某些受控环境下不可用。这里直接调 java 主类，效果等价。

用法：
    python start_neo4j.py            # 前台启动（Ctrl+C 停止）
    python start_neo4j.py --check    # 只测试连接，不启动
    python start_neo4j.py --admin dbms set-initial-password 你的密码
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# Neo4j 发行版放在项目内部，拷走项目就能跑
NEO4J_HOME = BASE_DIR / "neo4j" / "neo4j-enterprise-5.3.0"
# JDK 用的是本机已有的一份（Neo4j Desktop 自带的 zulu17），不在项目内；
# 找不到就退回环境变量 JAVA_HOME 或 PATH 上的 java。
DEFAULT_JAVA_HOME = Path(
    r"project\学习\\智能计算方向\neo4j\distributions\java\zulu17.38.21-ca-jdk17.0.5")
BOOT_CLASS = "org.neo4j.server.startup.Neo4jBoot"
ADMIN_CLASS = "org.neo4j.server.startup.Neo4jAdminBoot"


def _java_major(exe: str):
    """取 java 主版本号；取不到返回 None。"""
    try:
        out = subprocess.run([exe, "-version"], capture_output=True,
                             text=True, timeout=15,
                             encoding="utf-8", errors="ignore")
        text = (out.stderr or "") + (out.stdout or "")
        m = re.search(r'version "(\d+)(?:\.(\d+))?', text)
        if not m:
            return None
        major = int(m.group(1))
        # 1.8.0_xxx 这种老格式
        return 8 if major == 1 else major
    except Exception:
        return None


def _java() -> str:
    """
    JDK 解析顺序，逐个做版本校验（Neo4j 5.3 只认 Java 17）：

      1. KG_JAVA_HOME（应急用，跳过校验）
      2. 本机已有的 zulu17（最稳，已验证可用）
      3. 环境变量 JAVA_HOME —— 仅当它确实是 17
      4. PATH 上的 java —— 仅当它确实是 17

    注意：本机 JAVA_HOME 指向 jdk-22，直接拿来跑 Neo4j 会在
    UnsafeUtil.initDirectByteBuffer 抛 `UnsupportedOperationException: set`，
    所以不能盲信 JAVA_HOME。
    """
    override = os.environ.get("KG_JAVA_HOME")
    if override:
        exe = Path(override) / "bin" / "java.exe"
        if exe.exists():
            return str(exe)

    candidates = []
    if DEFAULT_JAVA_HOME and (DEFAULT_JAVA_HOME / "bin" / "java.exe").exists():
        candidates.append(str(DEFAULT_JAVA_HOME / "bin" / "java.exe"))
    if os.environ.get("JAVA_HOME"):
        candidates.append(str(Path(os.environ["JAVA_HOME"]) / "bin" / "java.exe"))
    which = shutil.which("java")
    if which:
        candidates.append(which)

    tried = []
    for exe in candidates:
        if not Path(exe).exists():
            continue
        major = _java_major(exe)
        if major == 17:
            return exe
        tried.append(f"{exe} (Java {major})")

    raise SystemExit(
        "找不到可用的 Java 17。试过：\n  " + ("\n  ".join(tried) or "无") +
        "\n可用 KG_JAVA_HOME 环境变量指定一个 JDK 17 目录。")


def _run(main_class: str, args, blocking=True):
    if not NEO4J_HOME.exists():
        raise SystemExit(f"找不到 Neo4j 发行版：{NEO4J_HOME}")
    java = _java()
    env = dict(os.environ)
    env["NEO4J_ACCEPT_LICENSE_AGREEMENT"] = "yes"
    # JAVA_HOME 指到实际使用的 JDK（java.exe 往上两级）
    env["JAVA_HOME"] = str(Path(java).resolve().parent.parent)
    cmd = [java, "-cp", "../lib/*;../plugins/*",
           "-Dfile.encoding=UTF-8", main_class] + list(args)
    # 必须在 bin 目录下执行：Neo4jBoot 按「当前目录的上一级」推算 NEO4J_HOME
    if blocking:
        return subprocess.call(cmd, cwd=str(NEO4J_HOME / "bin"), env=env)
    return subprocess.Popen(cmd, cwd=str(NEO4J_HOME / "bin"), env=env)


def check() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import config as C
    from neo4j import GraphDatabase
    try:
        d = GraphDatabase.driver(C.NEO4J_URI,
                                 auth=(C.NEO4J_USER, C.NEO4J_PASSWORD))
        d.verify_connectivity()
        with d.session(database=C.NEO4J_DATABASE) as s:
            n = s.run("MATCH (n) RETURN count(n)").single()[0]
            e = s.run("MATCH ()-[r]->() RETURN count(r)").single()[0]
            ver = s.run("CALL dbms.components() YIELD versions RETURN versions[0] AS v").single()[0]
        d.close()
        print(f"连接成功：{C.NEO4J_URI}  Neo4j {ver}")
        print(f"图规模：{n} 节点 / {e} 关系")
        return 0
    except Exception as ex:
        print(f"连接失败：{ex}")
        print("先把 Neo4j 起起来：python start_neo4j.py")
        return 1


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = sys.argv[1:]
    if args and args[0] == "--check":
        return check()
    if args and args[0] == "--admin":
        return _run(ADMIN_CLASS, args[1:])

    print(f"启动 Neo4j（{NEO4J_HOME.name}）…  按 Ctrl+C 停止")
    print("端口：bolt 7690 / routing 7691 / http 7476（避开 Neo4j Desktop 已占用的 7474/7687/7688）")
    try:
        return _run(BOOT_CLASS, ["console"])
    except KeyboardInterrupt:
        print("\n已停止")
        return 0


if __name__ == "__main__":
    sys.exit(main())
