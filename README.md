# 0元版：GitHub Actions + Server酱

## 目标

- 电脑关机也运行
- 不购买 VPS
- GitHub Actions 每 5 分钟触发一次扫描
- 符合“半球假强主队”条件才调用 Server酱微信推送
- 同一比赛只推一次

## 为什么使用 Public Repository

GitHub 官方说明：标准 GitHub-hosted runner 在 **public repository** 中免费。
因此这个方案的 GitHub Actions 运行成本可为 0 元。

> 代码公开，但 Server酱 SendKey 不放在代码里，而是放在 GitHub Actions Secret。

## 部署步骤

### 1. GitHub 新建 Public Repository

例如：

`nowscore-halfball-monitor`

仓库必须选择 **Public** 才按本方案保持 Actions 运行费用为 0。

### 2. 上传本目录所有文件

保持 `.github/workflows/monitor.yml` 的目录结构。

### 3. 配置 Server酱 Secret

进入仓库：

`Settings -> Secrets and variables -> Actions -> New repository secret`

Name：

`SERVERCHAN_SENDKEY`

Value：

填你的 Server酱 SCT SendKey。

### 4. 启用 Actions

进入 `Actions` 页面。如果 GitHub 要求确认启用 workflow，点击启用。

### 5. 手工测试一次

`Actions -> 半球假强主队监控 -> Run workflow`

运行成功后，以后会自动按 cron 调度。

## 当前规则

自动评分 6 项：

1. 当前主流亚洲盘 = 主 -0.5
2. 当前主胜中位数 1.90–2.10
3. 客 +0.5 十进制价 1.70–1.85
4. 主胜初盘→即时下降或保持强势
5. 客胜初盘→即时不降反升
6. 主流初盘/即时没有形成 -0.75 共识

默认 >= 4/6 并且数据可靠才推微信。

如果当前主流不是 -0.5，则即使其它条件凑够，也不会按原模型推送。

## 去重

`data/state.json` 保存已经推送的 match ID。

推送成功后 workflow 会自动提交 state 文件，所以后面的 5 分钟扫描不会重复推同一场。

## 60天自动停用问题

GitHub 官方说明：Public repository 如果 60 天没有 repository activity，
scheduled workflow 可能被自动禁用。

本项目额外提供 `.github/workflows/keepalive.yml`，每月更新一次 `.monitor-heartbeat`
并提交，用于让仓库保持活动。

## 注意

GitHub 的 schedule 不是实时系统。虽然 cron 最短可以设为 5 分钟，
高负载时实际启动时间可能发生延迟。因此它适合作为 0 元监控方案，
不应当当作“临场每秒级交易系统”。

Server酱免费版也有自身的每日推送额度；本程序只在匹配时推送，避免普通扫描消耗推送次数。
