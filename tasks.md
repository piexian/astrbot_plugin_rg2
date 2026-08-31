# qqoffice-expand-migration - Task List

## Implementation Tasks

- [x] 1. **core/qq_official.py 瘦身**
    - [x] 1.1. 删除裸调函数与 botpy 依赖
        - *Goal*: 模块只保留平台判断与游戏业务数据
        - *Details*: 删除 `get_qq_bot_client`/`ban_member`/`send_text`/`send_card` 与 `from botpy.http import Route`；保留 `is_qq_official_event`/`parse_interaction`/`QQInteractionShim`/键盘构造
        - *Requirements*: 需求 3、4
- [x] 2. **main.py svc 生命周期与门禁**
    - [x] 2.1. svc 绑定管理
        - *Goal*: 两种加载时序均可绑定/解绑
        - *Details*: `QQOFFICE_PLUGIN`/`INSTALL_HINT` 常量；`qq_svc`/`_qq_unsub`/`_last_card_msg` 成员；`_try_bind_qqoffice()`；`initialize()` 试绑；`on_plugin_loaded/unloaded` 广播处理（空装饰器兜底老版本）；`terminate()` 解绑并清理 `_last_card_msg`
        - *Requirements*: 需求 1
    - [x] 2.2. `_qq_gate` 门禁与落点
        - *Goal*: 官机 svc 未绑定时全功能仅回复安装提示
        - *Details*: `_qq_gate(event)`；落点：装填/开枪/左轮 状态/走火开/走火关/群消息走火监听；AI 工具 `run()` 返回 `PLATFORM_LIMIT` 文案
        - *Requirements*: 需求 5
- [ ] 3. **按钮回调迁移**
    - [x] 3.1. `_on_qqoffice_event` 替代旧钩子
        - *Goal*: 删除猴子补丁与手工 ack/intents，改走 svc 事件订阅
        - *Details*: 删 `_install_qq_interaction_hook`/`on_platform_loaded`/`_qq_client`/`_handle_interaction`；新 handler 解析 `raw.data.resolved.button_data`、群一致性校验、load/shoot/status 分支、shim(bot=None)
        - *Requirements*: 需求 2
- [x] 4. **发送链路（含卡片自动撤回）**
    - [x] 4.1. `_send_card` 统一入口
        - *Goal*: 卡片发送/撤回/跟踪单点收口
        - *Details*: `send_rich` 成功 → 撤回 `_last_card_msg` 旧卡（`svc.group.recall`，异常仅 debug）→ 记录新 id；失败返回 None
        - *Requirements*: 需求 3、6
    - [x] 4.2. `_reply_load_result`/`_reply_status_result`/`_send_group_text` 改造
        - *Goal*: 事件回复与主动文本改走 svc
        - *Details*: 回复条件加 `self.qq_svc`；`_send_group_text` 官机分支改 `svc.group.send`
        - *Requirements*: 需求 3
- [ ] 5. **禁言链路迁移**
    - [x] 5.1. `_ban_user` 官机分支改 `svc.group.mute_member`
        - *Goal*: 官方禁言接口走中台
        - *Details*: RFC3339 过期时间计算保留；异常维持 bot_admin/target_immune 分类；新增 svc_missing 分支文案；OneBot 分支不动
        - *Requirements*: 需求 4、5
- [ ] 6. **测试改写与验证**
    - [x] 6.1. 三个测试文件改写
        - *Goal*: 测试与新实现对齐
        - *Details*: `test_qq_official.py` 删裸调用例；`test_card_interaction.py` 改 fake svc 全流程（含撤回 4 用例）；`test_platform_branch.py` 禁言改 fake mute_member + 门禁用例
        - *Requirements*: 全部验收标准
    - [x] 6.2. 全量验证 + 文档收尾
        - *Goal*: 实现与文档同步交付
        - *Details*: `uv run pytest tests/ -v` 全绿；`ruff check`/`ruff format` 通过；同步 README（官机依赖说明）与 CHANGELOG v1.3.5 条目；单 commit 提交
        - *Requirements*: 非功能性要求（可回退、文档同步）

## Task Dependencies

- 任务 1 先行（决定 main.py 可用导入）
- 任务 2→3→4→5 串行（同文件 main.py，按序改）
- 任务 6 依赖 1-5 全部完成

## Estimated Timeline

- 任务 1: 0.5h
- 任务 2: 1h
- 任务 3: 1h
- 任务 4: 1h
- 任务 5: 0.5h
- 任务 6: 1.5h
- **Total: 5.5h**
