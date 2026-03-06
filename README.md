# MusicTagFixer

一个面向 Windows 的 MP3 标签/文件名修复工具，支持本地乱码修复和 Discogs OAuth 联网补全标签（title/artist/album/year/genre/track/composer 等）。

## 主要功能

- 批量扫描 MP3（支持递归）
- 修复文件名中的日文乱码
- 修复 ID3 标签乱码：
  - 标题 `TIT2`
  - 艺术家 `TPE1`
  - 专辑艺术家 `TPE2`
  - 专辑 `TALB`
  - 作曲 `TCOM`
- 批量设置 `genre`（`TCON`）：
  - `fill` 仅填空
  - `overwrite` 全覆盖
  - `merge` 合并补充
- Discogs OAuth 联网拉取并写回标签（含重试、超时和 token 缓存）
- 提供 Web GUI（推荐）和 CLI 两种方式

## 项目结构

```text
Scripts/
  fix_mp3_japanese_mojibake.py   # 本地乱码修复核心（CLI）
  discogs_tag_sync.py            # Discogs OAuth 联网补全标签（CLI）
  test_discogs_tag_sync.py       # Discogs 逻辑离线单元测试
  mp3_tag_webgui.py              # 本地 Web GUI
  mp3_tag_gui.py                 # Tk GUI（可选）
  start_webgui.bat               # 一键启动 Web GUI（推荐）
  start_gui.bat                  # 一键启动 Tk GUI（可选）
```

## 环境要求

- Windows 10/11
- Python 3.7+

安装依赖：

```powershell
pip install mutagen requests requests_oauthlib
```

## 快速开始（推荐：Web GUI）

1. 双击运行：

```text
Scripts\start_webgui.bat
```

2. 浏览器打开：

```text
http://127.0.0.1:8766/
```

3. 在页面中：

- 先点 **扫描常用目录**，从下拉框选择目标目录（会自动填充绝对路径）
- 默认先勾选 **仅预览 (Dry Run)** 看日志
- 确认结果正确后，取消 Dry Run 再正式执行

## 本地修复（CLI）

### 1) 先预览（推荐）

```powershell
python Scripts\fix_mp3_japanese_mojibake.py "C:\Users\<你用户名>\Music\目标目录" --dry-run --verbose
```

### 2) 正式写入

```powershell
python Scripts\fix_mp3_japanese_mojibake.py "C:\Users\<你用户名>\Music\目标目录" --verbose
```

### 3) 只改标签，不改文件名

```powershell
python Scripts\fix_mp3_japanese_mojibake.py "C:\Users\<你用户名>\Music\目标目录" --no-rename --verbose
```

### 4) 批量补 genre（只填空）

```powershell
python Scripts\fix_mp3_japanese_mojibake.py "C:\Users\<你用户名>\Music\目标目录" --set-genre "J-Pop" --genre-mode fill --verbose
```

## Discogs 联网补全标签（OAuth）

### 用前准备

1. 在 Discogs 创建应用，拿到：
   - `consumer key`
   - `consumer secret`
2. 建议先配置环境变量：

```powershell
$env:DISCOGS_CONSUMER_KEY="你的key"
$env:DISCOGS_CONSUMER_SECRET="你的secret"
```

### 第一次授权（只做 OAuth）

```powershell
python Scripts\discogs_tag_sync.py "C:\Users\<你用户名>\Music\目标目录" --auth-only
```

执行后会给出授权 URL，浏览器确认授权，输入 PIN/verifier。成功后会缓存 token 到：

```text
.discogs_oauth_token.json
```

### 联网写标（先 dry-run）

```powershell
python Scripts\discogs_tag_sync.py "C:\Users\<你用户名>\Music\目标目录" --dry-run --verbose
```

### 正式写入

```powershell
python Scripts\discogs_tag_sync.py "C:\Users\<你用户名>\Music\目标目录" --verbose
```

### 常用参数

- `--force-reauth`：强制重新 OAuth
- `--no-browser`：不自动打开浏览器
- `--no-recursive`：仅处理顶层目录
- `--connect-timeout` / `--read-timeout`：连接/读取超时
- `--http-retries` / `--retry-backoff`：重试次数/退避
- `--search-limit` / `--release-fetch-limit`：匹配搜索范围

## 可靠性设计（针对 OAuth 超时问题）

Discogs 联网模块已内置：

- token 本地缓存（避免每次重复 OAuth）
- 请求超时控制（连接/读取分离）
- 5xx/429/超时自动重试 + 退避
- 429 `Retry-After` 处理
- OAuth 成功后会调用 identity 接口验证 token 可用性

## 自测说明

已做离线单元测试（不依赖真实联网）：

```powershell
python Scripts\test_discogs_tag_sync.py
```

覆盖范围：

- track position 解析
- 匹配打分与最佳曲目选择
- 标签写入流程（dry-run）

## 常见问题

### 1) 文件名没变化

先确认：

- 是否勾选了/传入了“修复文件名”
- 是否还在 Dry Run（Dry Run 不会写盘）
- 日志是否出现 `skip rename (target exists)`（目标文件名已存在）

### 2) 页面看起来还是旧版本

- 先关闭旧终端服务，再重新运行 `start_webgui.bat`
- 浏览器按 `Ctrl + F5` 强制刷新
- 确认地址是 `http://127.0.0.1:8766/`

### 3) Discogs OAuth 失败或超时

- 先执行 `--auth-only` 单独完成授权
- 增大 `--connect-timeout` 和 `--read-timeout`
- 提高 `--http-retries`
- 必要时 `--force-reauth`

## 注意事项

- 建议先对音乐目录做备份
- 先 `Dry Run` 观察日志，再正式写入
- 大目录首次扫描可能较慢，属正常现象

## License

仓库已包含 `LICENSE` 文件，请按该许可证使用。
