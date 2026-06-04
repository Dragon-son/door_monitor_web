"""零散的入口路由:首页、健康检查、模板下载"""

import os
from datetime import datetime

from flask import Blueprint, send_from_directory

from routes._app import BASE
from routes.helpers import ok, err


bp = Blueprint("misc", __name__)


@bp.route("/")
def index():
    return send_from_directory(BASE, "access_control.html")


@bp.route("/api/health", methods=["GET"])
def health():
    return ok({"status": "running", "time": datetime.now().isoformat()})


@bp.route("/download/template")
def download_template():
    template_path = os.path.join(BASE, "user.xlsx")
    if not os.path.exists(template_path):
        return err("模板文件不存在", 404)
    return send_from_directory(
        BASE, "user.xlsx",
        as_attachment=True,
        download_name="user.xlsx"
    )
