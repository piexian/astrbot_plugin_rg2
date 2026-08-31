# qqoffice-expand-migration - Design Document

## Overview

官机能力从自实现 botpy 裸调切换为挂载 `astrbot_plugin_qqoffice_expand` 中台 svc。职责切分：rg2 只保留平台判断与游戏业务数据（按钮协议、键盘布局、Shim），官方 API 调用、事件订阅、intents、ack、频控、被动窗口全部委托 svc。svc 未绑定 = 官机功能停用 + 安装提示；OneBot 路径不触碰。

## Architecture

```
指令/AI/按钮事件
   │
   ▼
_qq_gate(event) ──svc None 且官机──▶ 回复安装提示，return
   │ 通过
   ▼
游戏逻辑（_do_load_game/_do_shoot_game/_do_status/_do_misfire，不变）
   │
   ▼
发送/禁言分支：
  OneBot ──▶ event.bot.set_group_ban / send_group_msg（不变）
  官机   ──▶ svc.send_rich / svc.group.send / svc.group.mute_member
```

svc 生命周期（按 expand README 标准模板，加载顺序不可控的两种时序都覆盖）：

```
initialize() ──▶ _try_bind_qqoffice()
                   ├─ 成功: qq_svc=star_cls; _qq_unsub=svc.on("INTERACTION_CREATE", _on_qqoffice_event)
                   └─ 失败: 记 info 日志，等广播
on_plugin_loaded(name==expand)  ──▶ 再次 _try_bind_qqoffice()
on_plugin_unloaded(name==expand)──▶ _qq_unsub(); qq_svc=None
terminate() ──▶ _qq_unsub()
```

不在 `initialize()` 阻塞等待；老版本 AstrBot 无 `filter.on_plugin_loaded/unloaded` 时用空装饰器兜底（模块级 `getattr` 探测），保证类定义不崩、整体按未安装处理。

## Components and Interfaces

### core/qq_official.py（瘦身，保留纯业务）

- 删除：`get_qq_bot_client`、`ban_member`、`send_text`、`send_card`、`from botpy.http import Route`。
- 保留不变：`is_qq_official_event`、`QQ_INTERACTION_PREFIX`、`_QQ_ACTIONS`、`parse_interaction`、`_make_button`、`build_game_keyboard`、`build_start_keyboard`、`QQInteractionShim`。

### main.py 新增

```python
QQOFFICE_PLUGIN = "astrbot_plugin_qqoffice_expand"
INSTALL_HINT = ("⚠️ 当前平台需安装 astrbot_plugin_qqoffice_expand 插件才能使用本游戏\n"
                "💡 插件市场搜索 qqoffice_expand，或从链接安装: "
                "https://github.com/piexian/astrbot_plugin_qqoffice_expand")

self.qq_svc = None        # expand 的 star_cls
self._qq_unsub = None     # INTERACTION_CREATE 解绑闭包

def _try_bind_qqoffice(self) -> bool: ...   # get_registered_star → activated+ready → 绑定+订阅
def _qq_gate(self, event) -> str | None: ... # 官机且 svc None → INSTALL_HINT，否则 None
async def _on_qqoffice_event(self, ev) -> None: ...  # 替代 _handle_interaction
```

### main.py 删除

`_install_qq_interaction_hook`、`on_platform_loaded`、`self._qq_client`、`_handle_interaction`（含手工 `on_interaction_result` ack 与手工 intents OR）。

### 按钮回调链路（`_on_qqoffice_event`）

1. `ev.is_interaction` 过滤；`ev.raw["data"]["resolved"]["button_data"]` → `parse_interaction` → 非本插件按钮直接 return。
2. 群一致性校验：`ev.group_openid` 与解析值不符 → 记 warning 并 return（ack 已由中台自动 code=0，不再手工应答）。
3. 按 action 复用现有逻辑：`load`→`_do_load_game(shim,...)`、`shoot`→`_do_shoot_game(shim,...)`、否则 `_do_status`；shim 构造为 `QQInteractionShim(None, group_openid, member_openid)`（bot 不再需要）。
4. 结果发送（统一走 `_send_card`，见「卡片自动撤回」节）：
   - `qq_card_enabled`：`_send_card(group_openid, content, kb, event_id=ev.payload_id)`；键盘按游戏状态二选一（进行中=开枪/状态，否则=快速开始）。
   - 卡片关闭或发送异常：`svc.group.send(group_openid, content, event_id=ev.payload_id)` 纯文本。

### 事件回复链路（装填/状态）

`_reply_load_result` / `_reply_status_result` 条件改为 `self.qq_card_enabled and self.qq_svc and is_qq_official_event(event)`，满足则 `_send_card(group_id, text, kb, event=event)` 返回非 None 即 `event.stop_event()`；否则走原文本逻辑。

### 卡片自动撤回

- 新增 `self._last_card_msg: dict[str, str]`（group_id → 本群上一条卡片 message_id），仅内存跟踪，随 `terminate()` 清理。
- 统一卡片发送入口：

```python
async def _send_card(self, group_id: str, content: str, keyboard: dict, *,
                     event: AstrMessageEvent | None = None,
                     event_id: str | None = None) -> dict | None:
    """官机卡片统一发送：成功后撤回上一条卡片并记录新 id；失败返回 None 由调用方降级。"""
```

- 流程：`svc.send_rich`（event 与 event_id 二选一：指令回复传 event 走 msg_id 被动；按钮回调传 `event_id=ev.payload_id` + `event_id_source="INTERACTION_CREATE"`）→ 成功取响应 `id` → `svc.group.recall(group_id, prev_id)` 撤回上一条 → 记录新 id。**先发后撤**，无空窗。
- 撤回失败（超 2 分钟窗口 / 消息已不存在）仅 debug 日志，不阻断主流程；纯文本降级路径不参与撤回跟踪。
- 落点：`_reply_load_result`、`_reply_status_result`、`_on_qqoffice_event` 三处统一走 `_send_card`。

### 禁言链路（`_ban_user` 官机分支）

```python
expire = (datetime.now(timezone.utc) + timedelta(seconds=duration)).isoformat(timespec="seconds")
await self.qq_svc.group.mute_member(group_id, user_id, expire)
```
- svc None（防御，正常不可达——指令入口已被 gate 拦截）：`_last_ban_error="svc_missing"`，返回 0。
- 异常分类维持现状：官方错误中文描述仍在 `QQOfficeAPIError.message`，继续按子串区分 `bot_admin` / `target_immune`。
- `_format_ban_failure` 新增 `svc_missing` 分支：「⚠️ 禁言失败！（需安装 qqoffice_expand 插件）」。

### 主动文本链路（`_send_group_text`，用于超时通知与 AI 消息）

```python
if hasattr(bot, "send_group_msg"):   # OneBot，不变
    await bot.send_group_msg(group_id=int(group_id), message=text)
elif self.qq_svc is not None:        # 官机
    await self.qq_svc.group.send(group_id, text)
else:
    logger.warning("官机 svc 未绑定，跳过主动消息")
```

### 门禁落点（全部指令与监听入口）

- 指令：`装填`/`开枪`/`左轮 状态`/`走火开`/`走火关` 入口处 `hint = self._qq_gate(event)`，非 None 则 `yield event.plain_result(hint); return`。
- 群消息走火监听：misfire 判定命中前先过 gate，命中即回复安装提示（不触发游戏逻辑）。
- AI 工具（`tools/revolver_game_tool.py`）：`run()` 注册触发器前先过 gate，命中则返回 `"PLATFORM_LIMIT: 当前平台需安装 astrbot_plugin_qqoffice_expand 插件，游戏功能不可用，请如实转告用户"`，由 LLM 在正常回复中传达（该回复走 AstrBot 自带管线，不依赖 svc）。

## Data Models

- 按钮协议不变：`rg2:<action>:<group_openid>`（`action ∈ {shoot, status, load}`），`parse_interaction` 不变。
- 键盘数据结构不变：`{"content": {"rows": [{"buttons": [...]}]}}`，调 svc 时包一层 `{"keyboard": ...}`；markdown 包 `{"markdown": {"content": ...}}`。
- `QQOfficeEvent` 使用字段：`raw`（取 `data.resolved.button_data`）、`group_openid`、`member_openid`、`payload_id`（作 `event_id`）、`is_interaction`。
- 游戏状态（`group_games`/`group_misfire`/超时任务/AI 队列）完全不变；持久化格式不变。
- 新增 `_last_card_msg: dict[str, str]`（group_id → 上一条卡片 message_id），仅内存跟踪不落盘，随 `terminate()` 清理。

## Error Handling

| 场景 | 处理 |
| --- | --- |
| svc 未绑定 + 官机事件 | 门禁统一回复 INSTALL_HINT，功能不启用 |
| `send_rich` 抛 `QQOfficeAPIError` | 记日志降级 `svc.group.send` 纯文本；再失败记 error |
| `mute_member` 抛 `QQOfficeAPIError` | 按 message 子串分 `bot_admin`/`target_immune`（沿用现有文案），其他记日志返回 0 |
| expand 运行中被卸载 | `on_plugin_unloaded` 解绑，svc 置 None，后续事件自动走门禁 |
| 按钮回调处理异常 | try/except 记 error，不再向上抛（ack 已由中台完成） |
| 老版本 AstrBot 无广播装饰器 | 空装饰器兜底，svc 恒 None，官机走门禁 |

## Testing Strategy

- `tests/test_qq_official.py`：删除 `ban_member`/`send_text`/`send_card`/`get_qq_bot_client` 用例；保留并修正 `is_qq_official_event`、`parse_interaction`、键盘构造、`QQInteractionShim` 用例。
- `tests/test_card_interaction.py`：整体改写为 fake svc（`on()` 捕获 handler、`send_rich`/`group.send`/`group.recall` 为 AsyncMock）驱动 `_on_qqoffice_event`：覆盖非本插件按钮忽略、群不一致忽略、load/shoot/status 三分支、卡片关闭降级纯文本、send_rich 异常降级；卡片撤回用例（第二张卡发出后 recall 第一张、recall 异常容忍、降级纯文本不触发撤回、`_last_card_msg` 随 terminate 清理）。
- `tests/test_platform_branch.py`：`test_ban_user_qqofficial*` 改为 fake `svc.group.mute_member`（含权限/免疫异常分类）；新增 svc 未绑定门禁用例（官机指令只回安装提示、OneBot 不受影响）。
- 验证命令：`uv run pytest tests/ -v` 全绿；`ruff check` 与 `ruff format --check` 通过。
