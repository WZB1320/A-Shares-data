"""Stock Data API - 应用入口

职责:
  1. 创建 FastAPI 应用 + CORS 中间件
  2. 注册所有 router(按业务域拆分到 api/routers/)
  3. 提供启动入口(端口探测 + uvicorn.run)

接口实现见 api/routers/{common,market,corporate,flow,master}.py
共享依赖见 api/deps.py

已下线接口(代码已清理):
  - /api/metadata/*    数据字典(原 src/metadata/ 已删除)
  - /api/quality/*     质量检查(原 src/quality/ 已删除)
"""
import socket
import sys
from pathlib import Path

# 兼容直接 `python api/main.py` 启动: 把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import (
    common,
    corporate,
    flow,
    market,
    master,
)

app = FastAPI(
    title="Stock Data API",
    version="1.0.0",
    description="A-Shares 数据采集与分析系统 REST API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 按业务域注册路由
app.include_router(common.router)
app.include_router(market.router)
app.include_router(corporate.router)
app.include_router(flow.router)
app.include_router(master.router)


if __name__ == "__main__":
    import os
    import uvicorn

    DEFAULT_HOST = os.environ.get("A_SHARES_API_HOST", "127.0.0.1")
    DEFAULT_PORT = int(os.environ.get("A_SHARES_API_PORT", "8001"))
    FALLBACK_PORTS = [DEFAULT_PORT + 1, DEFAULT_PORT + 2, DEFAULT_PORT + 3, DEFAULT_PORT + 4]

    def _port_available(host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return True
            except OSError:
                return False

    # 优先绑定用户指定 host(默认 127.0.0.1 避免 WinError 10013 权限问题);
    # 若用户请求 0.0.0.0 且被权限拦截,自动回退到 127.0.0.1
    host_candidates = []
    if DEFAULT_HOST == "0.0.0.0":
        host_candidates = ["0.0.0.0", "127.0.0.1"]
    elif DEFAULT_HOST == "127.0.0.1":
        host_candidates = ["127.0.0.1", "0.0.0.0"]
    else:
        host_candidates = [DEFAULT_HOST, "127.0.0.1"]

    port = DEFAULT_PORT
    bind_host: str | None = None
    for h in host_candidates:
        if _port_available(h, port):
            bind_host = h
            break
        # host 不可用(WinError 10013/10048 混合)时尝试备用端口
        for p in FALLBACK_PORTS:
            if _port_available(h, p):
                port = p
                bind_host = h
                break
        if bind_host:
            break
    else:
        print(
            f"错误: 端口 {DEFAULT_PORT}-{FALLBACK_PORTS[-1]} 均不可用,"
            f"或 host {','.join(host_candidates)} 均绑定失败(WinError 10013 请尝试以管理员运行)"
        )
        exit(1)

    # 确保 data 目录存在(即使 DB 文件还未生成,依赖注入也能优雅降级不抛 500)
    _data_dir = Path(__file__).resolve().parent.parent / "data"
    try:
        _data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    print(f"启动 API 服务: http://{bind_host}:{port}")
    print(f"Swagger 文档: http://localhost:{port}/docs")
    print("提示: 如需外网访问可设置环境变量 A_SHARES_API_HOST=0.0.0.0")
    uvicorn.run(app, host=bind_host, port=port)
