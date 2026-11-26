#!/usr/bin/env python3
"""向后兼容入口：实际主服务端已迁移至 app.py"""

from app import run_server

if __name__ == "__main__":
    run_server()
