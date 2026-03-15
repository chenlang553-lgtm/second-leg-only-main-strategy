# Second Leg Only Main Strategy

这是一个**完全独立**的 Python 策略项目，目标是尽量贴近 `polymarket-bot` 的 `main` 行为，但代码本身**不依赖** `polymarket-bot`。

它现在可以作为一个独立项目单独运行。

当前核心逻辑是：

- 沿用 `main` 的第一腿 `entry` 判定条件
- **第一腿不真实下单**
- 第一腿只用于建立一个虚拟锚点 `entry_anchor`
- 后续按接近 `main` 的优先级，真实执行：
  - `leg2_limit`
  - `early_lock`
  - `repair`
  - `insurance`
  - `loss_reduction`
  - `late_confirmation`

可以把它理解成：

- `main` 的第一腿变成“观察信号”
- `main` 的第二腿变成“唯一真实交易腿”

## 策略特征

1. 使用 `main` 的 probe / entry 条件
2. 记录虚拟第一腿：
   - 方向
   - 价格
   - 创建时间
   - 虚拟持仓
3. 后续真实推进虚拟组合状态
4. 按接近原版 `main` 的动作优先级做后续真实交易

## 目录

- `strategy.py`: 策略主类和数据结构
- `runner.py`: 独立 runner，可直接运行
- `trader.py`: Polymarket CLOB 下单封装
- `gamma.py`: 单市场元数据获取
- `market_data.py`: 独立 websocket 行情接入
- `requirements.txt`: 依赖说明

## 使用方式

### 1. 直接跑本地/纸面模式

```python
python3 /root/second-leg-only-main-strategy/runner.py \
  --input /path/to/snapshots.jsonl
```

### 2. 单市场实时模式

先准备环境变量：

```bash
export PRIVATE_KEY=...
export FUNDER_ADDRESS=你的钱包地址
export API_KEY=...
export API_SECRET=...
export PASSPHRASE=...
```

如果还没有 API 凭证，可以先派生一次：

```bash
python3 /root/second-leg-only-main-strategy/runner.py --derive-creds
```

然后启动：

```bash
python3 /root/second-leg-only-main-strategy/runner.py \
  --slug btc-updown-5m-1773558300 \
  --live
```

### 3. 自动跟随 BTC 5m 市场

```bash
python3 /root/second-leg-only-main-strategy/runner.py \
  --follow-btc-5m \
  --live \
  --keep-running
```

这会：

- 自动选择当前 BTC 5m 市场
- 到窗口结束后自动切到下一个 `btc-updown-5m-*`
- 每个市场重新初始化策略状态

### 4. JSONL 快照格式

每行一个 JSON：

```json
{
  "now_ms": 1773331800000,
  "time_to_expiry_sec": 180,
  "prices": {"up": 0.57, "down": 0.43},
  "scores": {"up": 0.61, "down": 0.39},
  "token_ids": {
    "up": "UP_TOKEN_ID",
    "down": "DOWN_TOKEN_ID"
  }
}
```

如果用 `--slug` 实时模式，就不需要手工提供 `token_ids`，runner 会自己从 Gamma 拉取 market 元数据。

### 5. 运行结果

runner 会：

- 逐条读入快照
- 调用策略 `on_snapshot`
- 一旦策略发出第二腿订单，就打印 action
- `--live` 模式下会真实调用 CLOB 下单
- 默认把这笔单视为立即成交并停止，方便单市场测试

如果返回 `None`，说明当前不下单。  
如果返回 `OrderAction`，就表示此时应该真实买入第二腿。

## 当前实现范围

当前版本已经包含这些主路径：

- 虚拟第一腿 `entry`
- 真实 `leg2_limit / early_lock`
- 真实 `repair / insurance / loss_reduction`
- 真实 `late_confirmation`

它仍然**不是** 100% 完整复刻原版 `main`。当前还没补完的主要是：

- 更完整的订单维护 / replace / cancel
- 更完整的 phase 机和维护动作
- `tail_hedge` 等外围尾盘保护
- 原版所有风控细节和日志解释

## 默认参数

参数对齐现有 `v18 main`：

- `entryWindowMinSec = 60`
- `entryWindowMaxSec = 300`
- `entryMinPrice = 0.55`
- `entryMaxPrice = 0.59`
- `entryScoreGapMin = 0.22`
- `probeConfirmMs = 5000`
- `entryClipShares = 5`
- `lockTargetPnl = 0.5`
- `earlyLockPairSumThreshold = 0.9`

## 注意

- 这是一个独立策略实现，不会改动原项目代码
- 它输出的是“该不该下第二腿”的交易动作
- 这版项目现在已经能**单独跑**，但它是一个独立 runner，不直接依赖原 bot
- 这版已经内置了真实下单接口，也已经支持单市场 websocket 实时模式
- 也支持自动轮转 BTC 5m 市场
- 真实下单使用的是 `market order + FAK`
- 下单金额当前固定为 `1 USDC`
- `price` 字段当前固定为 `0.6`，作为滑点保护上限
- 还没有 100% 复刻 `polymarket-bot main` 的全部行为，但已经不再是“只有第二腿的简化版”

## 日志行为

实时运行时会输出这些日志：

- `status`：仅当价格 / score / 剩余秒数发生变化时打印
- `heartbeat`：如果行情暂时没变化，每 5 秒确认一次进程仍活着
- `watchdog_reconnect`：超过 5 秒没有新的 websocket `book` 消息时，自动重连当前市场
- `order_action`：策略真正触发下单时打印
- `startup` / `rollover`：启动和市场轮转时打印
