"""python -m macroflow.ui.app 的入口（打包版同样走这里）。"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    # 以脚本方式直接运行（如开机自启动命令 python <src>/macroflow/ui/app/__main__.py）：
    # 把 src/ 加入导入路径，才能解析 macroflow.* 包。
    _SRC_ROOT = Path(__file__).resolve().parents[3]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from macroflow.ui.app.startup import main  # noqa: E402

if __name__ == '__main__':
    main()
