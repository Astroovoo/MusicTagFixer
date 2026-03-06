# MusicTagFixer

一个面向 Windows 的 MP3 标签/文件名修复工具，专门用于批量处理日文乱码（mojibake）和 `genre` 补全。

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
- 提供 Web GUI（推荐）和 CLI 两种使用方式

## 项目结构

```text
Scripts/
  fix_mp3_japanese_mojibake.py   # 核心修复逻辑（CLI）
  mp3_tag_webgui.py              # 本地 Web GUI
  mp3_tag_gui.py                 # Tk GUI（可选）
  start_webgui.bat               # 一键启动 Web GUI（推荐）
  start_gui.bat                  # 一键启动 Tk GUI（可选）
```

## 环境要求

- Windows 10/11
- Python 3.7+
- 依赖：`mutagen`

安装依赖：

```powershell
pip install mutagen
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

## CLI 用法

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

### 3) 扫描目录后还是找不到

可以直接手填目标目录绝对路径到输入框。

## 注意事项

- 建议先对音乐目录做备份
- 先 `Dry Run` 观察日志，再正式写入
- 大目录首次扫描可能较慢，属正常现象

## License

仓库已包含 `LICENSE` 文件，请按该许可证使用。
