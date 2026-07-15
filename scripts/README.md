# scripts/

运维与回归脚本（不进运行时热路径）。

## 构建 / 运维

| 路径 | 说明 |
|------|------|
| `build_admin_assets.py` | 管理台静态资源打包（`static/js` → `static/dist`） |
| `upgrade_from_file_backend.sh` | file 后端 → hybrid（PG/Redis）升级迁移 |

## 回归测试（`_test_*.py`）

本地直接跑，不依赖 pytest 插件：

```bash
python3 scripts/_test_task_status_terminal.py
python3 scripts/_test_cpa_affinity_improvements.py
python3 scripts/_test_strict_cooldown_rotation.py
python3 scripts/_test_rotation_load_spread.py
python3 scripts/_test_free_usage_hard_kick.py
```

| 路径 | 覆盖 |
|------|------|
| `_test_task_status_terminal.py` | TaskUpdate / Update 路径 / 终态帧 |
| `_test_cpa_affinity_improvements.py` | 会话粘性：Claude session / messages hash / model 隔离 / 清绑定 |
| `_test_strict_cooldown_rotation.py` | 冷却池硬排除 live 轮询 |
| `_test_rotation_load_spread.py` | pick-time inflight 负载分散 |
| `_test_free_usage_hard_kick.py` | 没额度立即冷却踢出 |

> 不要把一次性 release 脚本、第三方安装器、临时研究抓取脚本放进本目录。
