# qqoffice-expand-migration - Requirements Document

将 rg2 插件的 QQ 官方机器人能力（禁言、卡片消息、按钮回调）从自实现 botpy 裸调切换为挂载 astrbot_plugin_qqoffice_expand 中台插件；官机平台未安装中台时仅回复安装提示、不启用功能；OneBot 平台零影响。

## Core Features

1. **svc 绑定管理**：按 expand 标准模板接入——`initialize()` 先试绑，未就绪等 `on_plugin_loaded` 广播再绑，`on_plugin_unloaded` 解绑；绑定成功即 `svc.on("INTERACTION_CREATE", handler)` 订阅按钮回调。
2. **按钮回调迁移**：`_handle_interaction`（botpy interaction 对象）→ `_on_qqoffice_event(QQOfficeEvent)`；手工 ack 删除（中台自动应答 code=0）；手工 intents 注入删除（中台 AdapterPatcher 构造期注入 1<<26）。
3. **消息发送迁移**：卡片/文本发送从裸调 `api.post_group_message` 改为 `svc.send_rich`（被动 msg_id/event_id 自动补、频控、被动窗口超窗自动降级）。
4. **禁言迁移**：`ban_member`（裸调 `_http.request`）改为 `svc.group.mute_member(group_openid, member_openid, rfc3339_expire)`。
5. **未安装降级**：官机平台事件在 svc 未绑定时，所有指令仅回复安装提示，游戏功能（装填/开枪/状态/走火/AI 触发）不启用。
6. **卡片自动撤回**：新卡片发送成功后自动撤回本群上一条卡片（官方 2 分钟撤回窗口内），避免群界面堆积卡片；撤回失败不阻断流程。

## User Stories

- 作为官机群主，我希望按钮卡片交互由中台插件统一提供，以便获得稳定的频控与被动窗口管理。
- 作为未安装中台的官机用户，我使用指令时收到明确的安装提示，而不是功能静默失败或裸调报错。
- 作为 OneBot 用户，我不受任何影响，也不需要安装新插件。

## Acceptance Criteria

- [ ] `core/qq_official.py` 中 `get_qq_bot_client` / `ban_member` / `send_text` / `send_card` 及 `botpy` 导入全部移除；平台判断、`parse_interaction`、`QQInteractionShim`、键盘构造保留。
- [ ] `main.py` 无 `_install_qq_interaction_hook` / `_qq_client` / 手工 intents / 手工 ack；svc 绑定、广播重绑、卸载解绑齐全。
- [ ] 按钮回调链路：`svc.on("INTERACTION_CREATE")` → 解析 `raw.data.resolved.button_data` → 游戏逻辑 → `send_rich` 回卡（`event_id=payload_id`，`event_id_source="INTERACTION_CREATE"`）。
- [ ] 装填/状态回复在 svc 已绑定时走 `svc.send_rich(event, markdown, keyboard)`；未绑定时回复安装提示。
- [ ] 官机禁言走 `svc.group.mute_member`；失败仍区分「机器人无权限 / 对方免疫」文案；svc 未绑定时回复安装提示（归入禁言失败文案）。
- [ ] 官机平台 svc 未绑定时，`装填/开枪/左轮 状态/走火开/走火关/群消息走火/AI 触发` 均只回复安装提示；OneBot 路径完全不变。
- [ ] 官机卡片发送成功后自动撤回本群上一条卡片（`svc.group.recall`），撤回失败仅 debug 日志不影响流程；纯文本降级路径不参与撤回；跟踪状态随 `terminate()` 清理。
- [ ] 老版本 AstrBot（无 `filter.on_plugin_loaded`）下插件可正常加载（条件装饰器兜底），svc 不可用按未安装处理。
- [ ] `uv run pytest tests/` 全绿（含改写后的官机相关用例）；`ruff check` 通过。

## Non-functional Requirements

- 兼容性：OneBot（aiocqhttp）行为零变化；官机平台新增硬依赖 `astrbot_plugin_qqoffice_expand`（未安装=功能停用+提示）。
- 可回退：迁移收敛为单次提交，revert 即恢复旧实现。
- 可观测：绑定/解绑/降级均有日志；svc 未绑定的提示回复包含插件名与安装方式。
- 时序安全：不在 `initialize()` 内阻塞等待 svc；中台热安装后需重载一次官机适配器（intents 限制，属中台既有约束，文档提示）。
