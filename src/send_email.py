from __future__ import annotations

import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import List


ROOT = Path(__file__).resolve().parents[1]
LATEST_JSON = ROOT / "data" / "latest.json"


def getenv_required(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def parse_recipients(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def load_latest() -> dict:
    if not LATEST_JSON.exists():
        raise RuntimeError(f"latest.json not found: {LATEST_JSON}")
    return json.loads(LATEST_JSON.read_text(encoding="utf-8"))


def format_email_content(d: dict) -> tuple[str, str]:
    target_date = d.get("target_date_hkt", "")
    count = d.get("count", 0)
    items = d.get("items", []) or []
    generated_at = d.get("generated_at_hkt", "")
    source = d.get("source", "")
    debug = d.get("debug", {}) or {}

    subject = f"[HKEX AP] {target_date} 新增 {count} 家"

    lines = []
    lines.append("港交所前一日递表名单（AP）")
    lines.append(f"目标日期（HKT）：{target_date}")
    lines.append(f"生成时间（HKT）：{generated_at}")
    lines.append(f"数量：{count}")
    lines.append("")

    if count == 0:
        lines.append("昨日（HKT）无新增 AP 递表。")
    else:
        for i, item in enumerate(items, start=1):
            name = item.get("applicant_name", "Unknown")
            board = item.get("board", "Unknown")
            doc_type = item.get("doc_type", "Unknown")
            date_hkt = item.get("posting_date_hkt", "")
            link = item.get("link", "")

            lines.append(f"{i}. {name}")
            lines.append(f"   - 日期: {date_hkt}")
            lines.append(f"   - 类型: {doc_type}")
            lines.append(f"   - 板块: {board}")
            lines.append(f"   - 链接: {link}")
            lines.append("")

    lines.append("-----")
    lines.append(f"Source: {source}")
    if debug:
        lines.append(
            f"Debug: raw_rows={debug.get('raw_row_count')}, parsed_records={debug.get('parsed_record_count')}"
        )
        if debug.get("doc_type_distribution"):
            lines.append(f"DocTypeDist: {debug.get('doc_type_distribution')}")

    body = "\n".join(lines)
    return subject, body


def send_email(subject: str, body: str) -> None:
    smtp_host = getenv_required("SMTP_HOST")
    smtp_port = int(getenv_required("SMTP_PORT"))
    smtp_user = getenv_required("SMTP_USER")
    smtp_pass = getenv_required("SMTP_PASS")
    email_to = parse_recipients(getenv_required("EMAIL_TO"))
    email_from = os.getenv("EMAIL_FROM", "").strip() or smtp_user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = ", ".join(email_to)
    msg.set_content(body, charset="utf-8")

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except Exception:
                pass
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

    print(f"Email sent to: {email_to}")


def main() -> None:
    d = load_latest()
    subject, body = format_email_content(d)
    send_email(subject, body)


if __name__ == "__main__":
    main()
